#!/usr/bin/env python3
"""Split servekit single-file service protos into the api-repo three-file
layout (service.proto / message.proto / enums.proto [+ request_response.proto]),
hoisting Pong to common.v1 and (for testkit) replacing mirrored enums with
references to the owning domain's enums.

Robust version: brace counting ignores comments; mirrored enums are detected
by name intersection with the other domains' enums, not by comment wording.
"""
import os
import re
import sys

WKT = {
    "Empty": "google/protobuf/empty.proto",
    "Timestamp": "google/protobuf/timestamp.proto",
    "Duration": "google/protobuf/duration.proto",
    "Struct": "google/protobuf/struct.proto",
    "Any": "google/protobuf/any.proto",
    "FieldMask": "google/protobuf/field_mask.proto",
}


def strip_comment(line):
    """Remove // comment portion (no block comments in these protos)."""
    idx = line.find("//")
    return line if idx < 0 else line[:idx]


def parse_blocks(src):
    lines = src.split("\n")
    blocks = []
    pending = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if s == "" or s.startswith("//"):
            pending.append(line)
            i += 1
            continue
        if re.match(r"^(syntax|package|option)\b", s):
            blocks.append(("header", line + "\n"))
            pending = []
            i += 1
            continue
        if re.match(r"^import\s+", s):
            blocks.append(("import", line + "\n"))
            pending = []
            i += 1
            continue
        m = re.match(r"^(enum|message|service|extend)\s+[\.\w]+", s)
        if m:
            kind = m.group(1)
            name = s.split()[1]
            depth, body, j = 0, [], i
            while j < n:
                body.append(lines[j])
                code = strip_comment(lines[j])
                depth += code.count("{") - code.count("}")
                if depth <= 0 and "{" in "".join(strip_comment(x) for x in body):
                    break
                j += 1
            text = "\n".join(pending) + "\n" + "\n".join(body) + "\n"
            blocks.append((f"{kind}:{name}", text))
            pending = []
            i = j + 1
            continue
        blocks.append(("other", line + "\n"))
        pending = []
        i += 1
    return blocks


def classify(blocks):
    enums, messages, reqresp, services = [], [], [], []
    for kind, text in blocks:
        if ":" not in kind:
            continue
        k, name = kind.split(":", 1)
        if k == "enum":
            enums.append((name, text))
        elif k == "message":
            if name.endswith("Request") or name.endswith("Response"):
                reqresp.append((name, text))
            else:
                messages.append((name, text))
        elif k == "service":
            services.append((name, text))
    return enums, messages, reqresp, services


def imports_for(body, symbols, own_path):
    # scan code only (strip comments) so declarations/comments don't
    # self-import or drag in unreferenced files
    code = "\n".join(strip_comment(l) for l in body.split("\n"))
    imports = set()
    for name, path in symbols.items():
        # (?<!\w) matches both bare `Gender` and qualified `user.v1.Gender`
        if path != own_path and re.search(rf"(?<!\w){name}\b", code):
            imports.add(path)
    for name, path in WKT.items():
        if re.search(rf"google\.protobuf\.{name}\b", code):
            imports.add(path)
    if "(buf.validate." in code:
        imports.add("buf/validate/validate.proto")
    if "(google.api.http)" in code:
        imports.add("google/api/annotations.proto")
    return imports


def write(out_dir, domain, fname, doc, body, symbols, mapping):
    # Pong moved to common.v1 — qualify every reference (bare cross-package
    # references don't resolve in proto).
    mapping = dict(mapping)
    mapping["Pong"] = "common"
    for name, dom in mapping.items():
        body = re.sub(rf"(?<!\w){name}\b", f"{dom}.v1.{name}", body)
    own_path = f"{domain}/v1/{fname}"
    imports = imports_for(body, symbols, own_path)
    hdr = f"// {doc}\nsyntax = \"proto3\";\n\npackage {domain}.v1;\n"
    if imports:
        hdr += "\n" + "\n".join(f'import "{p}";' for p in sorted(imports)) + "\n"
    with open(os.path.join(out_dir, fname), "w") as f:
        f.write(hdr + "\n" + body.strip("\n") + "\n")
    print(f"  {fname}: {len(imports)} imports")


def main():
    base = sys.argv[1]  # servekit root
    # "message" collides with the proto keyword `message` — cross-package
    # references like `message.v1.EmailVendor` cannot parse. The domain is
    # reborn as `messaging` in the api repo (wire-breaking, internal-only
    # consumers; testkit's own contract is unaffected).
    pkg_of = {"message": "messaging"}
    parsed = {}
    for dom in ["gid", "user", "license", "storage", "message", "telemetry", "testkit"]:
        src = open(f"{base}/{dom}-service/api/proto/{dom}/v1/{dom}.proto").read()
        parsed[dom] = classify(parse_blocks(src))

    # cross-domain enum name -> owning domains (by output package name)
    owners = {}
    for dom, (enums, *_rest) in parsed.items():
        for name, _ in enums:
            owners.setdefault(name, []).append(pkg_of.get(dom, dom))

    for dom, (enums, messages, reqresp, services) in parsed.items():
        pkg = pkg_of.get(dom, dom)
        out_dir = f"{base}/api/{pkg}/v1"
        os.makedirs(out_dir, exist_ok=True)

        mapping = {}
        if dom == "testkit":
            # Ambiguous name collisions resolved by inspection: testkit's
            # SortField mirrors storage (FILENAME/SIZE values) and is
            # deliberately reused for message lists — the message facade
            # int-casts it to message.v1.SortField (values 0/1 identical).
            manual = {"SortField": "storage"}
            for name, text in enums:
                cand = [d for d in owners.get(name, []) if d != "testkit"]
                if name in manual:
                    if manual[name] not in cand:
                        sys.exit(f"manual hint wrong for {name}: {cand}")
                    mapping[name] = manual[name]
                elif len(cand) == 1:
                    mapping[name] = cand[0]
                elif len(cand) > 1:
                    m = re.search(r"Mirrors\s+(\w+)-service", text)
                    if m and m.group(1) in cand:
                        mapping[name] = m.group(1)
                    else:
                        sys.exit(f"ambiguous enum {name}: owned by {cand}")
            if mapping:
                print(f"  {dom}: {len(mapping)} mirrored enums -> domain refs")
            enums = [(n, t) for n, t in enums if n not in mapping]

        # Pong is common now
        pong_msgs = [n for n, _ in messages if n == "Pong"]
        messages = [(n, t) for n, t in messages if n != "Pong"]
        assert not pong_msgs or pong_msgs == ["Pong"]

        symbols = {}
        for n, _ in enums:
            symbols[n] = f"{pkg}/v1/enums.proto"
        for n, _ in messages:
            symbols[n] = f"{pkg}/v1/message.proto"
        for n, _ in reqresp:
            symbols[n] = f"{pkg}/v1/request_response.proto"
        symbols["Pong"] = "common/v1/pong.proto"
        for name, d in mapping.items():
            symbols[name] = f"{d}/v1/enums.proto"

        def joined(items):
            return "\n\n".join(t.strip("\n") for _, t in items) + "\n" if items else ""

        D = pkg.capitalize()
        if enums:
            write(out_dir, pkg, "enums.proto",
                  f"{D} domain enums. Owned by this domain; enums referenced "
                  f"by >= 2 domains belong in common/v1 instead.",
                  joined(enums), symbols, mapping)
        if messages:
            write(out_dir, pkg, "message.proto",
                  f"{D} domain messages (entities / value objects).",
                  joined(messages), symbols, mapping)
        if reqresp:
            write(out_dir, pkg, "request_response.proto",
                  f"{D} RPC request/response payloads.",
                  joined(reqresp), symbols, mapping)
        if services:
            write(out_dir, pkg, "service.proto",
                  f"{D} service definitions — RPC declarations only.",
                  joined(services), symbols, mapping)


if __name__ == "__main__":
    main()
