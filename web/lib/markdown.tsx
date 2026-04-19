/**
 * 极简 markdown 渲染器 · v2.5.4。
 *
 * 设计原则：
 * - 零依赖：项目保持「只有 next + react + tailwind」的极简运行时
 * - 安全：所有输出走 React 元素（children 自动转义），无 dangerouslySetInnerHTML
 * - 流式鲁棒：未闭合的 inline 标记（`**` 单开、`[text](...` 未闭合）退化为纯文本
 * - 调性一致：继承父级 fraunces-body；仅用 paper/ink/accent/rule 四组 token
 *
 * 支持的语法（对话里最常见）：
 *   块级：段落、空行分段、# / ## / ### 标题、- / * 无序列表、1. 有序列表、
 *         > 引用、--- 分割线
 *   行内：**粗体**、*斜体* / _斜体_、`行内代码`、[文本](链接)
 *
 * 不做的事（YAGNI）：
 *   - 代码块 ``` 三反引号：对话里少见；且流式期间未闭合态极易扰乱视觉
 *   - 表格 / 删除线 / 任务列表：GFM 扩展，对话里几乎不用
 *   - HTML 内联：安全面不值得
 */
import { Fragment, type ReactNode } from "react";

// ---------------------------------------------------------------------------
// Inline parser
// ---------------------------------------------------------------------------

type InlineRule = {
  pattern: RegExp;
  render: (m: RegExpExecArray, keyPrefix: string) => ReactNode;
};

// 注意：每条规则的 pattern 只匹配「完整闭合」的结构；未闭合的会整个 fallthrough
// 到纯文本分支，从而在流式过程中不产生崩溃或诡异半形。
const INLINE_RULES: InlineRule[] = [
  // 行内代码 —— 第一优先级，code 里不再递归解析其他标记
  {
    pattern: /`([^`\n]+)`/,
    render: (m, k) => (
      <code
        key={k}
        className="font-mono text-[0.9em] bg-accent/[0.08] text-ink px-1.5 py-[1px] rounded-[3px] border border-accent/15"
      >
        {m[1]}
      </code>
    ),
  },
  // 链接 [text](url) —— 只允许 http(s) / mailto，防 javascript: 类 XSS
  {
    pattern: /\[([^\]\n]+)\]\(([^)\s]+)\)/,
    render: (m, k) => {
      const href = m[2];
      const safe = /^(https?:|mailto:|\/)/i.test(href) ? href : "#";
      const external = /^https?:/i.test(safe);
      return (
        <a
          key={k}
          href={safe}
          className="text-accent underline underline-offset-2 decoration-accent/40 hover:decoration-accent transition-colors"
          target={external ? "_blank" : undefined}
          rel={external ? "noopener noreferrer" : undefined}
        >
          {renderInline(m[1], `${k}:a`)}
        </a>
      );
    },
  },
  // 粗体 **text** —— 至少 1 字符，不跨段落
  {
    pattern: /\*\*([^*\n]+(?:\*(?!\*)[^*\n]*)*)\*\*/,
    render: (m, k) => (
      <strong key={k} className="font-semibold text-ink">
        {renderInline(m[1], `${k}:b`)}
      </strong>
    ),
  },
  // 斜体 *text* 或 _text_ —— 避免和粗体撞：粗体在前匹配；这里要求单侧单星
  {
    pattern: /(?<![*\w])\*([^*\n]+)\*(?!\*)|(?<![_\w])_([^_\n]+)_(?!_)/,
    render: (m, k) => (
      <em key={k} className="italic text-ink">
        {renderInline(m[1] ?? m[2] ?? "", `${k}:i`)}
      </em>
    ),
  },
];

/**
 * 把一段 inline 文本解析为 ReactNode[]。
 *
 * 策略：反复在 rest 中找最靠左的规则命中；命中前的段按纯文本输出；命中部分替换为
 * 对应 React 元素；继续处理剩余。找不到任何命中则剩余整段作为纯文本输出。
 */
export function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  if (!text) return [];
  const nodes: ReactNode[] = [];
  let rest = text;
  let counter = 0;

  while (rest.length > 0) {
    let bestIdx = -1;
    let bestMatch: RegExpExecArray | null = null;
    let bestRule: InlineRule | null = null;

    for (const rule of INLINE_RULES) {
      // 每次 new 一个 RegExp 实例，避免 lastIndex 污染
      const re = new RegExp(rule.pattern.source, rule.pattern.flags.replace("g", ""));
      const m = re.exec(rest);
      if (m && m.index !== undefined) {
        if (bestIdx === -1 || m.index < bestIdx) {
          bestIdx = m.index;
          bestMatch = m;
          bestRule = rule;
        }
      }
    }

    if (!bestMatch || !bestRule || bestIdx < 0) {
      nodes.push(rest);
      break;
    }

    if (bestIdx > 0) nodes.push(rest.slice(0, bestIdx));
    nodes.push(bestRule.render(bestMatch, `${keyPrefix}-${counter++}`));
    rest = rest.slice(bestIdx + bestMatch[0].length);
  }

  return nodes;
}

// ---------------------------------------------------------------------------
// Block parser
// ---------------------------------------------------------------------------

type Block =
  | { kind: "p"; text: string }
  | { kind: "h"; level: 1 | 2 | 3; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "hr" };

const RE_H = /^(#{1,3})\s+(.+)$/;
const RE_HR = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const RE_UL = /^\s*[-*]\s+(.*)$/;
const RE_OL = /^\s*\d+\.\s+(.*)$/;
const RE_QUOTE = /^\s*>\s?(.*)$/;

function isBlockStarter(line: string): boolean {
  return (
    RE_H.test(line) ||
    RE_HR.test(line) ||
    RE_UL.test(line) ||
    RE_OL.test(line) ||
    RE_QUOTE.test(line)
  );
}

function parseBlocks(src: string): Block[] {
  const lines = src.split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // 空行 → 段落分隔
    if (!line.trim()) {
      i++;
      continue;
    }

    // 标题
    const h = line.match(RE_H);
    if (h) {
      blocks.push({ kind: "h", level: h[1].length as 1 | 2 | 3, text: h[2] });
      i++;
      continue;
    }

    // 分割线
    if (RE_HR.test(line)) {
      blocks.push({ kind: "hr" });
      i++;
      continue;
    }

    // 无序列表
    if (RE_UL.test(line)) {
      const items: string[] = [];
      while (i < lines.length) {
        const m = lines[i].match(RE_UL);
        if (!m) break;
        items.push(m[1]);
        i++;
      }
      blocks.push({ kind: "ul", items });
      continue;
    }

    // 有序列表
    if (RE_OL.test(line)) {
      const items: string[] = [];
      while (i < lines.length) {
        const m = lines[i].match(RE_OL);
        if (!m) break;
        items.push(m[1]);
        i++;
      }
      blocks.push({ kind: "ol", items });
      continue;
    }

    // 引用
    if (RE_QUOTE.test(line)) {
      const buf: string[] = [];
      while (i < lines.length) {
        const m = lines[i].match(RE_QUOTE);
        if (!m) break;
        buf.push(m[1]);
        i++;
      }
      blocks.push({ kind: "quote", text: buf.join("\n") });
      continue;
    }

    // 段落：聚合连续非空、非其它块起始行（软换行保留）
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim() && !isBlockStarter(lines[i])) {
      buf.push(lines[i]);
      i++;
    }
    blocks.push({ kind: "p", text: buf.join("\n") });
  }

  return blocks;
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function renderBlock(block: Block, key: string): ReactNode {
  switch (block.kind) {
    case "p":
      return (
        <p
          key={key}
          className="fraunces-body text-[20px] leading-[1.62] tracking-tight text-ink whitespace-pre-wrap"
        >
          {renderInline(block.text, key)}
        </p>
      );
    case "h": {
      // 调性克制：标题不喧宾夺主。h1 稍大 + accent；h2/h3 近 body，仅通过字重 + 字重变化区分
      if (block.level === 1) {
        return (
          <h3
            key={key}
            className="fraunces-body text-[22px] leading-[1.4] tracking-tight text-accent font-semibold mt-2"
          >
            {renderInline(block.text, key)}
          </h3>
        );
      }
      if (block.level === 2) {
        return (
          <h4
            key={key}
            className="fraunces-body text-[20px] leading-[1.45] tracking-tight text-ink font-semibold mt-1"
          >
            {renderInline(block.text, key)}
          </h4>
        );
      }
      return (
        <h5
          key={key}
          className="font-mono text-[11px] tracking-widest uppercase text-ink-muted"
        >
          {renderInline(block.text, key)}
        </h5>
      );
    }
    case "ul":
      return (
        <ul key={key} className="pl-0 my-1 space-y-1.5 list-none">
          {block.items.map((item, idx) => (
            <li
              key={`${key}-${idx}`}
              className="relative pl-6 fraunces-body text-[20px] leading-[1.62] tracking-tight text-ink"
            >
              <span
                aria-hidden
                className="absolute left-0 top-[0.55em] w-[10px] h-px bg-accent/60"
              />
              {renderInline(item, `${key}-${idx}`)}
            </li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol key={key} className="pl-0 my-1 space-y-1.5 list-none">
          {block.items.map((item, idx) => (
            <li
              key={`${key}-${idx}`}
              className="relative pl-8 fraunces-body text-[20px] leading-[1.62] tracking-tight text-ink"
            >
              <span
                aria-hidden
                className="absolute left-0 top-[0.22em] font-mono text-[11px] tracking-wider text-accent/80 tabular-nums"
              >
                {String(idx + 1).padStart(2, "0")}
              </span>
              {renderInline(item, `${key}-${idx}`)}
            </li>
          ))}
        </ol>
      );
    case "quote":
      return (
        <blockquote
          key={key}
          className="pl-4 border-l-2 border-accent/40 fraunces-body-soft italic text-[19px] leading-[1.65] text-ink-soft whitespace-pre-wrap"
        >
          {renderInline(block.text, key)}
        </blockquote>
      );
    case "hr":
      return (
        <hr
          key={key}
          className="border-0 border-t border-rule/60 my-4"
          aria-hidden
        />
      );
    default: {
      const _exhaustive: never = block;
      return _exhaustive;
    }
  }
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

interface MarkdownProps {
  source: string;
  /** 末尾追加的节点（用于 streaming 游标）。 */
  trailing?: ReactNode;
  /** 整体容器额外 class；内部 block 间距由 space-y 控制。 */
  className?: string;
}

/**
 * 渲染一段 markdown 文本。
 *
 * - 空输入返回 `null`（配合流式 thinking 占位使用）
 * - `trailing` 会挂在最后一个 block 的末尾；若最后一个 block 是段落，会塞到 <p> 内，
 *   让游标紧贴最后一个字符；否则挂在容器末尾。
 */
export function Markdown({ source, trailing, className }: MarkdownProps) {
  const text = source ?? "";
  if (!text) return null;
  const blocks = parseBlocks(text);
  if (blocks.length === 0) return null;

  const lastIdx = blocks.length - 1;
  return (
    <div className={`space-y-3 ${className ?? ""}`}>
      {blocks.map((b, i) => {
        const key = `b${i}`;
        if (i === lastIdx && trailing && b.kind === "p") {
          // 游标与最后一个字符同行
          return (
            <p
              key={key}
              className="fraunces-body text-[20px] leading-[1.62] tracking-tight text-ink whitespace-pre-wrap"
            >
              {renderInline(b.text, key)}
              <Fragment>{trailing}</Fragment>
            </p>
          );
        }
        return renderBlock(b, key);
      })}
      {trailing && blocks[lastIdx]?.kind !== "p" ? (
        <div className="leading-none">{trailing}</div>
      ) : null}
    </div>
  );
}
