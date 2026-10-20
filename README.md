# skills-management

盘点、体检并批量描述一整个 skill 集合，产出两份固定结构的文档。

skill 集合会长大、会被删、会被互相引用。删掉一个 skill 之后，其他 skill 里指向它的引用
会**静默断裂**——没有任何报错，直到某天某个流程莫名其妙跑不动了。逐个翻目录靠人眼查，
既慢又不可靠。

这个 skill 把这件事变成一条可重复的流水线：**扫描 → 读 → 判 → 写 → 核验 → 报**。

## 产出

| 文件 | 路径 | 策略 |
|---|---|---|
| 说明文档 | `~/.agents/skills/skills-description.md` | 每次**直接覆盖**，不备份、不留历史版本 |
| 体检清单 | `~/.agents/skills/skills-health.md` | 每次**直接覆盖** |

两份文档的章节结构固定，因此两次整理之间可以直接 diff。

- **`skills-description.md`** — 六章：总览 / 主表 / 关系图 / 依赖矩阵 / 组合工作流 / 注意事项
- **`skills-health.md`** — 四分区：必处理 / 建议处理 / 仅记录（需人工判断）/ 已处理

## 安装

```bash
git clone https://github.com/vegetable-kun/skills-management.git \
  ~/.agents/skills/skills-management
```

无依赖，Python 3 标准库即可。

## 用法

```
/skill:skills-management 整理一下 skills
```

skill 禁用了模型自动调用（`disable-model-invocation: true`），必须显式输入 `/skill:skills-management`。
不接受参数。

## 11 项体检规则

**9 项必做**（脚本可判定 + 模型判断）：

| # | 规则 | 判定方式 |
|---|---|---|
| 1 | 失效引用 | 脚本给候选，模型读正文定性 |
| 2 | 命名不一致 | 模型 |
| 3 | 功能重叠 | 模型（须给出具体证据） |
| 5 | frontmatter 缺失 / description 过弱 | 脚本 |
| 6 | SKILL.md 超长（> 500 行） | 脚本 |
| 7 | 硬编码绝对路径 | 脚本 |
| 9 | 引用写法不统一（`/name` vs `` `name` ``） | 脚本 |
| 10 | 共享产物文件不一致（词汇表分叉） | 脚本 |
| 11 | 编码损坏（U+FFFD） | 脚本 |

**2 项仅记录**（不下结论）：孤儿 skill、疑似指令冲突。

> 脚本只做「找」，模型做「判」。脚本给的是候选项，不是结论。

## 闸门模式

`scan.py --gate` 只收高置信度问题，有问题则退出码为 1，可挂 commit hook 或 CI：

```bash
python3 scripts/scan.py --gate
```

它回答的是「改完还有没有硬伤」。**通过不代表没有功能重叠、指令冲突或孤儿 skill**——
那三项只有读过正文才能判断。

## 核验模式

写完产物后用 sha256 逐文件比对，证明「没有动过被检查的 skill」：

```bash
python3 scripts/scan.py --out /tmp/before.json     # 写前
# ... 写产物 ...
python3 scripts/scan.py --out /tmp/after.json      # 写后
python3 scripts/scan.py --compare /tmp/before.json # clean: true ?
```

## 边界

这个 skill **只报告，不修复**：

- 只能写 `skills-description.md` 和 `skills-health.md`，其他 skill 文件一律只读
- 建议动作写成**交给人执行的命令**，skill 自己不执行
- 不删除 skill、不新建 skill（那属于 `skill-creator` 的职责）
- 区分「事实」（行数、路径、引用名）与「线索」（重叠、冲突、孤儿）

## 文件结构

```
skills-management/
├── SKILL.md                  流程与硬规则
├── references/
│   ├── checks.md             11 项检查规则 + 误报排除 + 报告纪律
│   └── doc-template.md       六章骨架 + 清单分区
├── scripts/
│   └── scan.py               扫描 / --gate 闸门 / --compare 核验
└── evals/
    └── evals.json            3 条测试提示 + 18 条可验证期望
```

## 设计要点

**只认 `<dir>/SKILL.md` 为 skill 入口**，附属文件只统计数量与文件名，不读内容——避免噪音。

**递归扫描但附属文件只统计**：既不漏嵌套 skill，又不会把无关内容灌进上下文。

**脚本与模型的分工**是整个设计的地基：机械可判定的交给脚本，语义判断交给读正文的模型。
早期版本把「建议动作」写成待办清单，结果 agent 直接动手改了 10 个被检查的 skill——
现在 `references/checks.md` 的列名是「交给人执行的动作」，`SKILL.md` 的第 5 步是 sha256 核验。

## 相关

- `skill-creator` —— 创建 / 评测 / 优化**单个** skill
- `skills-management` —— 盘点 / 体检**整个** skill 集合（本 skill）
