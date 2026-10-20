#!/usr/bin/env python3
"""
scan.py — 机械扫描 skills 目录，输出结构化 JSON 供模型解读。

设计原则（与 skill-creator 一致）：
  脚本只做「找」，模型做「判」。
  凡是可机械判定的检查（frontmatter、行数、跨 skill 引用、硬编码路径、
  附属文件统计、嵌套关系、共享产物文件）都在这里完成；语义判断（功能重叠、
  指令冲突、孤儿 skill）留给模型。

用法:
    python3 scan.py [--skills-dir DIR] [--out FILE]
    python3 scan.py --compare PREV.json      # 核验「未修改被检查的 skill」
    python3 scan.py --gate                   # CI / 提交前闸门，有问题则退出码 1

输出 JSON 结构:
    {
      "skills_dir": str,
      "scanned_at": iso8601,
      "excluded_files": [str],
      "skills": [ {...}, ... ],
      "artifacts": [ {...}, ... ],   # 跨 skill 共享的产物文件
      "gate": {...},                # 仅 --gate 时出现
      "warnings": [str]
    }

每个 skill 记录:
    name, dir, skill_md, depth, parent_skill,
    frontmatter: {name, description, disable_model_invocation, ok, issues[]},
    lines, body_lines, description_chars,
    attached_files[], attached_count,
    referenced_skills[],          # 正文中提到的其他 skill 名
    unknown_slash_refs[],         # /xxx 形式但目录中不存在 -> 失效引用候选
    unknown_name_refs[],         # `xxx` 反引号名式但目录中不存在 -> 失效引用候选
    hardcoded_paths[],            # 写死的绝对路径
    flags[]                      # 脚本给出的机械告警
    notes[]                      # 给模型的提示（需语义判断）
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

# 扫描规则（与 SKILL.md 中的「排除清单」保持一致）
EXCLUDED_FILENAMES = {"skills-description.md", "skills-health.md"}

# 500 行 = skill-creator 给出的 SKILL.md 正文预算上限
MAX_SKILL_LINES = 500

# description 合理长度区间（字符）
MIN_DESC_CHARS = 40
MAX_DESC_CHARS = 1200

# 形如 /xxx 的引用里，常见的系统路径与文件后缀会被误判，需要排除
SLASH_STOPWORDS = {
    # 系统路径
    "tmp", "var", "usr", "etc", "opt", "srv", "mnt", "media", "home", "root",
    "bin", "dev", "proc", "sys", "run", "boot", "lib", "lib64", "sbin",
    "Users", "Windows", "Program", "Users", "app", "data", "workspace",
    # 扩展名 / 常见文件名
    "md", "txt", "json", "yaml", "yml", "toml", "sh", "py", "js", "jsx", "ts",
    "tsx", "css", "html", "xml", "csv", "lock", "log", "env", "git", "sql",
    # 工具 / 协议 / 常见词
    "npm", "node", "pnpm", "yarn", "python", "pip", "http", "https", "www",
    "api", "localhost", "usr", "bin", "api-docs", "health", "metrics",
    "status", "login", "logout", "admin", "docs", "help", "v1", "v2",
}

# 反引号名式引用（`skill-name`）的假名白名单：文档里举例用的假想 skill 名
NAME_REF_ALLOWLIST = {"research-helper", "research-helper-v2"}

# 反向提及：明确说「不要用 /xxx」的行不算失效引用
NEGATIVE_MENTION_RE = re.compile(
    r"(do not use|don't use|do not|never use|not use|\(not |不存在的|不要用|反例|反面)", re.I)

# 模板占位符：/iteration-N、/<name>-<ts> 之类
PLACEHOLDER_REF_RE = re.compile(r"-[A-Z]$|-\d+$|<|>")

# 硬编码绝对路径（允许路径中含空格，截到行尾/引号/括号后清洗）
POSIX_PATH_RE = re.compile(r"(?<![\w.~])/(?:mnt|home|Users|opt|srv|media|var|etc|usr|Applications)(?:/[^\n`\"'()\[\]]*)+")
WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\n`\"'()\[\]]*")
SLASH_REF_RE = re.compile(r"(?<![\w/.<-])/([a-zA-Z][a-zA-Z0-9._-]{2,})")

# 反引号包裹的 kebab-case 标识符（候选 skill 引用）
BACKTICK_NAME_RE = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)+)`")

# 形如 /foo.md 的文件名引用不是 skill
FILE_EXT_RE = re.compile(r"\.(md|py|json|txt|ya?ml|sh|ts|tsx?|jsx?|html|css|lock|toml)$")

# 项目根级的 .md 文件名（不含路径）—— 这类文件是「跨 skill 共享的规范产物」
# 注意：反引号包裹的形式（`CONTEXT.md`）是最常见的写法，必须能匹配到
ROOT_MD_RE = re.compile(r"(?<![\w/.-])([A-Za-z0-9_][A-Za-z0-9_.-]*\.md)\b")

# 「规范产物」的判定：全大写/下划线风格（CONTEXT.md、DECISION_MAP.md）。
# 只有这种命名才是团队共享的规范文件，Title Case / 小写的 .md 多半是普通笔记或示例
CANONICAL_ARTIFACT_RE = re.compile(r"^[A-Z][A-Z0-9_]*\.md$")

# 「词汇表类」规范文件——真正会跳 skill 共享、因此最容易出现两份同义文件的一类。
# 只对这一类做「自我闭环」告警，避免把 teach/codebase-design 的自有工作区文件也拖进来
GLOSSARY_LIKE = {
    "CONTEXT.md", "UBIQUITOUS_LANGUAGE.md", "GLOSSARY.md", "VOCABULARY.md",
    "DOMAIN_MODEL.md", "DOMAIN_GLOSSARY.md", "TERMS.md", "LANGUAGE.md",
}

# 产出/消费动词窗口
WRITE_VERBS = re.compile(
    r"\b(write|writes|writing|save|saves|saving|create|creates|creating|emit|emits|"
    r"generate|generates|generating|output|outputs|produce|produces|producing|"
    r"add|adds|update|updates|update d|append|appends)\b|写入|产出|保存|创建|新增|更新|追加", re.I)
READ_VERBS = re.compile(
    r"\b(read|reads|reading|load|loads|loading|consult|consults|see|refer|referenced|"
    r"depends on|uses|use|using|check|checks|from)\b|读取|参考|依赖|检查|来自|依据", re.I)

# 「声明式」定义：列表项开头就是文件名（- `X.md`: 说明……）。
# 这类行是在声明本 skill 拥有/创建该文件，即使句中没有明确的 write 动词
BULLET_DEF_RE = re.compile(r"^\s*[-*]\s+`?([A-Za-z0-9_][A-Za-z0-9_.-]*\.md)`?\s*[:：—-]")

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# U+FFFD 替换字符：编码损坏的痕迹（如 GBK/UTF-8 互转时丢字）
MOJIBAKE_CHAR = "\ufffd"

# 本脚本随 skills-management 分发：它自身的示例引用不算问题
SELF_EXEMPT_SKILL = "skills-management"


def parse_frontmatter(text):
    """解析 YAML frontmatter。只支持顶层 key: value 与 `key: |` 块，不引入外部依赖。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    raw = m.group(1)
    data = {}
    key = None
    buf = []
    for line in raw.split("\n"):
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if km and not line.startswith((" ", "\t", "-")):
            if key is not None:
                data[key] = "\n".join(buf).strip()
            key, val = km.group(1), km.group(2).strip()
            if val in ("|", ">", "|-", ">-"):
                buf = []
            else:
                data[key] = val.strip("\"'")
                key = None
                buf = []
        elif key is not None:
            buf.append(line)
    if key is not None:
        data[key] = "\n".join(buf).strip()
    return data


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def line_of(text, index):
    return text.count("\n", 0, index) + 1


def collect_skill_files(skills_dir):
    """只认 <dir>/SKILL.md 为 skill 入口。返回 (路径列表, 排除文件列表)。"""
    found, excluded = [], []
    for root, dirs, files in os.walk(skills_dir):
        dirs.sort()
        if "SKILL.md" in files:
            found.append(os.path.join(root, "SKILL.md"))
        for f in files:
            if f in EXCLUDED_FILENAMES and root == skills_dir:
                excluded.append(os.path.join(root, f))
    found.sort()
    return found, excluded


def attached_files_of(skill_dir):
    """同目录下的附属文件与子目录（只统计，不读内容）。"""
    out = []
    for name in sorted(os.listdir(skill_dir)):
        if name == "SKILL.md":
            continue
        p = os.path.join(skill_dir, name)
        if os.path.isdir(p):
            out.append(name + "/")
        else:
            out.append(name)
    return out


def hardcoded_paths(text):
    hits = []
    seen = set()
    for rx in (POSIX_PATH_RE, WIN_PATH_RE):
        for m in rx.finditer(text):
            p = m.group(0).strip().rstrip(".,;:-").rstrip()
            if not p or p in seen:
                continue
            seen.add(p)
            hits.append({"path": p, "line": line_of(text, m.start())})
    return hits


def line_text(text, lineno):
    lines = text.split("\n")
    return lines[lineno - 1] if 0 < lineno <= len(lines) else ""


def artifact_roles(text):
    """判断正文里每个「项目根级 .md 文件名」是被产出还是被消费。"""
    produced, consumed = set(), set()
    for m in ROOT_MD_RE.finditer(text):
        token = m.group(1)
        if token.lower() in ("readme.md", "agents.md", "claude.md", "skill.md"):
            continue
        window = text[max(0, m.start() - 160):m.end() + 80]
        if WRITE_VERBS.search(window):
            produced.add(token)
        if READ_VERBS.search(window):
            consumed.add(token)
    # 列表项里的文件定义 = 声明本 skill 拥有该文件
    for line in text.split("\n"):
        bm = BULLET_DEF_RE.match(line)
        if bm:
            produced.add(bm.group(1))
    return produced, consumed


def scan_one(skill_md, skills_dir, known_names, all_skill_dirs):
    text = open(skill_md, encoding="utf-8", errors="replace").read()
    skill_dir = os.path.dirname(skill_md)
    fm = parse_frontmatter(text)
    body = text[FRONTMATTER_RE.match(text).end():] if FRONTMATTER_RE.match(text) else text

    flags, notes, issues = [], [], []

    name = (fm or {}).get("name", "")
    desc = (fm or {}).get("description", "")
    dmi = str((fm or {}).get("disable-model-invocation", "")).strip().lower() in ("true", "yes", "1")

    if fm is None:
        issues.append("缺少 YAML frontmatter")
        flags.append("frontmatter_missing")
    else:
        if not name:
            issues.append("frontmatter 缺少 name")
            flags.append("frontmatter_missing_name")
        if not desc:
            issues.append("frontmatter 缺少 description（无法判断何时触发）")
            flags.append("frontmatter_missing_description")
        else:
            if len(desc) < MIN_DESC_CHARS:
                issues.append(f"description 仅 {len(desc)} 字符，偏短，可能难以正确触发")
                flags.append("description_too_short")
            if len(desc) > MAX_DESC_CHARS:
                issues.append(f"description 达 {len(desc)} 字符，过长，触发判定噪音大")
                flags.append("description_too_long")

    basename = os.path.basename(skill_dir)
    if name and name != basename:
        issues.append(f"frontmatter name ('{name}') 与目录名 ('{basename}') 不一致")
        flags.append("name_dir_mismatch")

    total_lines = text.count("\n") + 1
    body_lines = body.count("\n") + 1
    if total_lines > MAX_SKILL_LINES:
        issues.append(f"SKILL.md 共 {total_lines} 行，超过 {MAX_SKILL_LINES} 行渐进披露预算")
        flags.append("skill_md_too_long")

    # 编码损坏：U+FFFD 替换字符
    mojibake = []
    for i, line in enumerate(text.split("\n"), 1):
        if MOJIBAKE_CHAR in line:
            mojibake.append({"line": i, "count": line.count(MOJIBAKE_CHAR),
                              "text": line.strip()[:120]})
    content_issues = []
    if mojibake:
        total_moji = sum(m["count"] for m in mojibake)
        content_issues.append(f"正文含 {total_moji} 个 U+FFFD 替换字符（编码损坏，常见于 GBK/UTF-8 互转丢字）")
        flags.append("mojibake")

    # 嵌套关系
    depth, parent = 0, None
    parent_dir = os.path.dirname(skill_dir)
    while parent_dir.startswith(skills_dir) and parent_dir != skills_dir:
        if os.path.exists(os.path.join(parent_dir, "SKILL.md")):
            depth += 1
            parent = os.path.basename(parent_dir)
            break
        parent_dir = os.path.dirname(parent_dir)

    # 跨 skill 引用：正文中出现的其他 skill 名
    referenced = []
    for other in known_names:
        if other == name:
            continue
        if re.search(r"(?<![\w-])" + re.escape(other) + r"(?![\w-])", body):
            referenced.append(other)

    # 失效引用候选 A：/xxx 形式但不在已知 skill 名中
    unknown = {}
    for m in SLASH_REF_RE.finditer(body):
        token = m.group(1)
        if token in SLASH_STOPWORDS or token in known_names:
            continue
        # 模板占位符（如 <tmpdir>/architecture-review-<ts>.html 里的 architecture-review-）
        if token.endswith("-"):
            continue
        # 文件名引用（/SKILL.md、/scripts/scan.py）不是 skill
        if FILE_EXT_RE.search(token):
            continue
        # 后面紧跟路径分隔符 -> 是路径/目录名的一部分
        if m.end() < len(body) and body[m.end()] == "/":
            continue
        unknown.setdefault(token, []).append(line_of(body, m.start()))

    unknown_refs = []
    for k, v in sorted(unknown.items()):
        if name == SELF_EXEMPT_SKILL:
            continue  # 本 skill 自身的举例引用，按规则不计
        if PLACEHOLDER_REF_RE.search(k):
            continue  # 模板占位符
        lines_txt = " ".join(line_text(body, n) for n in v)
        if NEGATIVE_MENTION_RE.search(lines_txt):
            continue  # 明确说「不要用」的反向提及
        unknown_refs.append({"ref": "/" + k, "lines": v})
    if unknown_refs:
        notes.append("unknown_slash_refs 需语义判断：是失效引用、待建 skill，还是普通路径")

    # 失效引用候选 B：`xxx` 反引号名式但不在已知 skill 名中
    unknown_names = {}
    for m in BACKTICK_NAME_RE.finditer(body):
        token = m.group(1)
        if token in known_names or token in NAME_REF_ALLOWLIST:
            continue
        ln = line_of(body, m.start())
        if "skill" not in line_text(body, ln).lower():
            continue  # 只有在提到 skill 的行上才算引用候选
        unknown_names.setdefault(token, []).append(ln)
    unknown_name_refs = [{"ref": k, "lines": v} for k, v in sorted(unknown_names.items())]
    if unknown_name_refs:
        notes.append("unknown_name_refs 需语义判断：反引号名式引用了不存在的 skill")

    # 写法不一致：已知 skill 名却写成 /xxx 形式
    style_issues = []
    for other in sorted(known_names):
        for m in re.finditer(r"(?<![\w-])/" + re.escape(other) + r"(?![\w-])", body):
            style_issues.append({"ref": "/" + other, "line": line_of(body, m.start())})
    if style_issues:
        flags.append("slash_style_ref")
        notes.append("已知 skill 被写成 /xxx 形式，应统一为 `skill-name` 反引号名式")

    paths = hardcoded_paths(body)
    if paths:
        flags.append("hardcoded_path")

    att = attached_files_of(skill_dir)

    produced, consumed = artifact_roles(body)
    # skill 自己的附属文件就是它产出的（如 codebase-design/DESIGN-IT-TWICE.md）
    for att_name in att:
        if att_name.endswith(".md"):
            produced.add(att_name)

    rel = os.path.relpath(skill_dir, skills_dir)
    if rel.startswith(".") or "node_modules" in rel.split(os.sep):
        notes.append("该 skill 位于隐藏目录或 node_modules 内，请确认是否应被计入")

    return {
        "name": name or basename,
        "dir": skill_dir,
        "skill_md": skill_md,
        "relative_dir": rel,
        "sha256": sha256_of(skill_md),
        "depth": depth,
        "parent_skill": parent,
        "frontmatter": {
            "name": name,
            "description": desc,
            "disable_model_invocation": dmi,
            "ok": not issues,
            "issues": issues,
        },
        "lines": total_lines,
        "body_lines": body_lines,
        "description_chars": len(desc),
        "attached_files": att,
        "attached_count": len(att),
        "referenced_skills": sorted(referenced),
        "unknown_slash_refs": unknown_refs,
        "unknown_name_refs": unknown_name_refs,
        "style_issues": style_issues,
        "artifacts_produced": sorted(produced),
        "artifacts_consumed": sorted(consumed),
        "hardcoded_paths": paths,
        "mojibake": mojibake,
        "content_issues": content_issues,
        "flags": flags,
        "notes": notes,
    }


def build_artifacts(skills):
    """跨 skill 共享的产物文件：谁产出、谁消费、是否有人消费却无人产出。"""
    producers, consumers = {}, {}
    for s in skills:
        for a in s["artifacts_produced"]:
            producers.setdefault(a, set()).add(s["name"])
        for a in s["artifacts_consumed"]:
            consumers.setdefault(a, set()).add(s["name"])
    out = []
    for token in sorted(set(producers) | set(consumers)):
        p, c = producers.get(token, set()), consumers.get(token, set())
        canonical = bool(CANONICAL_ARTIFACT_RE.match(token))
        orphan = bool(c) and not p
        # 只被自己的产出方使用 —— 词汇表类规范文件本该跳 skill 共享，
        # 这种「自我闭环」往往意味着存在第二份同义文件
        self_only = canonical and token in GLOSSARY_LIKE and bool(p) and c.issubset(p)
        if not canonical:
            verdict = "— 非规范命名（仅供模型参考，不作为闸门依据）"
        elif orphan:
            verdict = "✗ 被消费但集合内无人产出（可能是词汇表/产物命名分叉）"
        elif self_only:
            verdict = "⚠ 词汇表类规范文件只被自己的产出方使用，未跳 skill 共享（可能存在第二份同义文件）"
        elif p:
            verdict = "✓ 有产出方"
        else:
            verdict = "— 无人消费"
        out.append({
            "artifact": token,
            "canonical": canonical,
            "producers": sorted(p),
            "consumers": sorted(c),
            "orphan_consumer": orphan and canonical,
            "self_only": self_only,
            "verdict": verdict,
        })
    return out


def build_gate(skills, artifacts):
    """闸门判定：只收高置信度问题，避免语义判断混进来。"""
    problems = []
    for s in skills:
        # 自身举例只豁免「引用类」检查；乱码、硬编码路径等与举例无关，照常报
        exempt_refs = s["name"] == SELF_EXEMPT_SKILL
        for iss in s["frontmatter"]["issues"]:
            problems.append({"kind": "frontmatter", "skill": s["name"], "detail": iss})
        for iss in s.get("content_issues", []):
            problems.append({"kind": "content", "skill": s["name"], "detail": iss})
        if s["hardcoded_paths"]:
            paths = ", ".join(sorted({p["path"] for p in s["hardcoded_paths"]}))
            problems.append({"kind": "hardcoded_path", "skill": s["name"], "detail": paths})
        if exempt_refs:
            continue
        for r in s["unknown_slash_refs"]:
            problems.append({"kind": "broken_ref", "skill": s["name"],
                             "detail": f"{r['ref']} @ L{','.join(map(str, r['lines']))}"})
        for r in s["unknown_name_refs"]:
            problems.append({"kind": "broken_ref", "skill": s["name"],
                             "detail": f"`{r['ref']}` @ L{','.join(map(str, r['lines']))}"})
        if s["style_issues"]:
            problems.append({"kind": "style_ref", "skill": s["name"],
                             "detail": ", ".join(i["ref"] for i in s["style_issues"])})
    for a in artifacts:
        if a["orphan_consumer"]:
            problems.append({"kind": "orphan_artifact", "skill": ", ".join(a["consumers"]),
                             "detail": f"{a['artifact']} 被消费但无人产出"})
    warnings = []
    for a in artifacts:
        if a["self_only"]:
            warnings.append(f"{a['artifact']} 只被 {', '.join(a['producers'])} 自己使用——"
                            f"确认这是有意保留的旧名（迁移说明），还是仍有一份没被读到的词汇表")
    return {"ok": not problems, "problem_count": len(problems), "problems": problems,
            "warnings": warnings}


def main():
    ap = argparse.ArgumentParser(description="扫描 skills 目录并输出结构化 JSON")
    ap.add_argument("--skills-dir", default=os.path.expanduser("~/.agents/skills"),
                    help="skills 根目录（默认 ~/.agents/skills）")
    ap.add_argument("--out", default=None, help="JSON 输出路径（默认 stdout）")
    ap.add_argument("--compare", default=None,
                    help="与先前一次 scan.json 比对，输出哪些 skill 被新增/删除/改动（用于核验'未修改被检查的 skill'）")
    ap.add_argument("--gate", action="store_true",
                    help="闸门模式：存在高置信度问题时退出码为 1（用于提交前 / CI 检查）")
    args = ap.parse_args()

    skills_dir = os.path.abspath(os.path.expanduser(args.skills_dir))
    if not os.path.isdir(skills_dir):
        sys.exit(f"目录不存在: {skills_dir}")

    skill_files, excluded = collect_skill_files(skills_dir)
    if not skill_files:
        sys.exit(f"未在 {skills_dir} 找到任何 SKILL.md")

    # 先建名字表，才能识别跨 skill 引用
    known_names = set()
    for p in skill_files:
        fm = parse_frontmatter(open(p, encoding="utf-8", errors="replace").read())
        if fm and fm.get("name"):
            known_names.add(fm["name"])
        known_names.add(os.path.basename(os.path.dirname(p)))

    skills = [scan_one(p, skills_dir, known_names, skill_files) for p in skill_files]
    skills.sort(key=lambda s: (s["depth"], s["name"]))
    artifacts = build_artifacts(skills)

    warnings = []
    nested = [s["name"] for s in skills if s["depth"] > 0]
    if nested:
        warnings.append(f"存在嵌套 skill（应另设章节，不混入主表）: {', '.join(nested)}")
    if excluded:
        warnings.append(f"已排除产物文件: {', '.join(os.path.basename(e) for e in excluded)}")

    result = {
        "skills_dir": skills_dir,
        "scanned_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "excluded_files": excluded,
        "skills": skills,
        "artifacts": artifacts,
        "warnings": warnings,
    }

    if args.gate:
        gate = build_gate(skills, artifacts)
        result["gate"] = gate
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        sys.exit(0 if gate["ok"] else 1)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.compare:
        prev = json.load(open(args.compare, encoding="utf-8"))
        prev_map = {s["name"]: s.get("sha256") for s in prev.get("skills", [])}
        cur_map = {s["name"]: s.get("sha256") for s in skills}
        added = sorted(set(cur_map) - set(prev_map))
        removed = sorted(set(prev_map) - set(cur_map))
        modified = sorted(n for n in set(cur_map) & set(prev_map)
                          if cur_map[n] and prev_map[n] and cur_map[n] != prev_map[n])
        report = {
            "added": added, "removed": removed, "modified": modified,
            "clean": not (added or removed or modified),
            "verdict": "被检查的 skill 未被修改 ✓" if not (added or removed or modified)
                       else "✗ 以下 skill 在本次运行中被改动，必须还原：" + ", ".join(added + removed + modified),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0 if report["clean"] else 1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"已写入 {args.out}（{len(skills)} 个 skill，{len(nested)} 个嵌套）")
    else:
        print(text)


if __name__ == "__main__":
    main()
