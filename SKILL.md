---
name: skills-management
description: 盘点、体检并批量描述 ~/.agents/skills/ 下的 skill 集合，产出 skills-description.md（功能/内容/适用领域/关联/触发方式/产物）与 skills-health.md（体检待办清单）。当用户说「整理 skills」「skill 说明文档」「盘点 skills」「skill 体检」「这些 skill 有重复吗」「更新 skills-description」时使用。不要用于：只读某一个已知 skill 文件；新建或改写某个 skill 的内容（那用 skill-creator）；用某个 skill 去干活（那用目标 skill 本身）。
disable-model-invocation: true
---

# Skills Management

对一个 skill **集合**做盘点与体检，产出两份固定结构的文档。

这个 skill 存在的理由：skill 集合会长大、会被删、会互相引用。删掉一个 skill 之后，
其他 skill 里指向它的引用会**静默断裂**（如 2026-10-20 之前 `decision-mapping`
引用着不存在的 `/grilling` 与 `/prototype`；现已统一为反引号名式并修正）。
逐个翻目录靠人眼查，既慢又不可靠。

## 输出

| 文件 | 路径 | 覆盖策略 |
|---|---|---|
| 说明文档 | `~/.agents/skills/skills-description.md` | **每次直接覆盖旧文件**，不备份、不留历史版本 |
| 体检清单 | `~/.agents/skills/skills-health.md` | **每次直接覆盖旧文件** |

覆盖前先读旧文件算出**变化摘要**（新增/删除的 skill、断链数量），覆盖后把摘要打印到对话里——
覆盖本身不等于静默，用户需要一眼看到这次整理动了什么。

## 流程

### 1. 扫描

```bash
python3 <skill-dir>/scripts/scan.py --out /tmp/skill-scan-before.json
```

脚本负责**机械可判定**的部分：枚举所有 `SKILL.md`、校验 frontmatter、统计行数、
统计附属文件、抽取跨 skill 引用（`/name` 与 `` `name` `` 两种写法）、检测硬编码路径、
识别嵌套关系、梳理跨 skill 共享的产物文件、记录每个 skill 的
`sha256`（**这个校验和是第 5 步核验的依据**）。

扫描规则：**只认 `<dir>/SKILL.md` 为 skill 入口**，并显式排除 `skills-description.md`
与 `skills-health.md` 两个产物文件。附属文件**只统计数量与文件名，不读内容**。

```bash
# 闸门模式：存在高置信度问题则退出码为 1（可挂 commit hook / CI）
python3 <skill-dir>/scripts/scan.py --gate
```

### 2. 读

读 `/tmp/skill-scan-before.json` 里列出的每个 `SKILL.md`（包括嵌套的），读它们的正文。
「内容」列要求提炼**正文里的机制**，不是复述 description——
所以必须读，不能只靠脚本输出。

### 3. 判

对 11 项体检规则做判断。前 9 项（失效引用、命名不一致、功能重叠、frontmatter、
超长、硬编码路径、引用写法不统一、共享产物文件不一致、编码损坏）必须完成；
后 2 项（孤儿 skill、疑似指令冲突）只记录线索，不下结论。

每项的**判定标准、误报排除、报告写法**见 `references/checks.md`——**判断前先读它**。

脚本给的是**候选**，不是结论。`unknown_slash_refs` 里的 `/grilling` 是失效引用，
还是 `skill-creator` 里 "Do NOT use `/skill-test`" 那种反向提及，还是路径片段？
只有读过原文才能区分。`unknown_name_refs`（反引号名式）同理——
**引用名改了不代表引用还在**，`/grilling` 改成 `` `grill-me` `` 之后，
真正的断链会藏到 `unknown_name_refs` 里去。

### 4. 写

按 `references/doc-template.md` 的**固定 6 章结构**写 `skills-description.md`，
按同一文件的 B 节写 `skills-health.md`。结构固定是为了让两次整理之间可以 diff。

**这两个文件是你唯一被允许写的文件。** 其他一切都是只读的。

模板文件定义了：主表 6 列各自写什么、关系图怎么分组、依赖矩阵的三列含义、
体检清单的分区与勾选框规则。**动笔前先读它**，避免自创结构。

### 5. 核验

写完产物后，**证明**"没有动过被检查的 skill"：

```bash
python3 <skill-dir>/scripts/scan.py --out /tmp/skill-scan-after.json
python3 <skill-dir>/scripts/scan.py --compare /tmp/skill-scan-before.json
```

`--compare` 逐个比对每个 `SKILL.md` 的 `sha256`，输出 `added` / `removed` / `modified`。
只要 `clean` 不为 true，就说明你（或你调用的子代理）**改动了被检查的 skill**——
**立刻还原，并把这件事写进最终总结**，不要隐瞒。

把你的核验结果写进最终回复：被检查的 skill 未被修改 ✓（N 个文件校验和一致）。

### 6. 报

在对话里打印：

- 本次扫描的 skill 总数、嵌套数
- 变化摘要：相比旧文档，新增 / 删除了哪些 skill，断链数量变化
- 体检结论：必做项的条数与要点，指向 `skills-health.md` 的具体行

## 硬规则

1. **你只能写两个文件：`skills-description.md` 和 `skills-health.md`。** 其他一切只读。
   这不是建议，是**边界**——第 5 步会用 sha256 逐文件核验，并把结果写进你的总结。
2. **建议动作是写给人看的，不是待办清单。** `skills-health.md` 的"建议动作"要写成
   **交给人执行的命令**（放代码块里），并注明"由你执行，本 skill 不执行"。
   你自己**不要执行它们**。**修好一个断链不是你的职责**——你唯一的产出是那两份文档。
3. **不删除 skill。** 功能重叠时该留哪个是使用习惯问题。给出确切命令，由人执行：
   `rm -rf ~/.agents/skills/<name>`
4. **不新建 skill。** 那属于 `skill-creator` 的职责。
5. **区分事实与线索。** 行数、路径、引用名是事实；重叠、冲突、孤儿是线索。
   措辞上不要把线索写成结论。
6. **每次都覆盖，不追加。** 用户明确要求直接覆盖旧的 `skills-description.md`。
   变化信息靠第 6 步的摘要呈现，不靠保留历史文件。

## 何时读哪个 reference

| 文件 | 什么时候读 |
|---|---|
| `references/checks.md` | 第 3 步「判」之前，以及写体检结论时 |
| `references/doc-template.md` | 第 4 步「写」之前 |

## 手动运行

```bash
# 扫描到文件
python3 scripts/scan.py --out /tmp/skill-scan.json

# 核验：有没有改动过被检查的 skill
python3 scripts/scan.py --compare /tmp/skill-scan.json

# 闸门：存在高置信度硬伤时退出码为 1
python3 scripts/scan.py --gate

# 直接看摘要
python3 scripts/scan.py | python3 -c "
import json,sys
d=json.load(sys.stdin)
for s in d['skills']:
    print(s['name'], s['lines'], s['attached_count'], s['unknown_slash_refs'], s['unknown_name_refs'])
print('--- 共享产物 ---')
for a in d['artifacts']:
    if a['canonical']: print(a['artifact'], '| 产出:', a['producers'], '| 消费:', a['consumers'], '|', a['verdict'])
"
```
