/**
 * 本地信件历史 · 只存在浏览器 localStorage，后端不感知。
 *
 * 用户在首页看到"之前写过的信 / 拿到的报告"，点进去能回看或再看报告。
 * 换一台设备 / 清缓存 → 历史消失。这是有意为之：我们不想在服务端做账号。
 */

const STORAGE_KEY = "oriself:letters:v1";
const MAX_ENTRIES = 10;

export interface LocalLetterEntry {
  letterId: string;
  createdAt: number;  // ms
  updatedAt: number;  // ms · 列表排序依据
  roundCount: number;
  status: "active" | "completed";
  /** 报告相关（完成后填） */
  issueSlug?: string;
  mbtiType?: string;
  cardTitle?: string;
}

function isBrowser(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function safeRead(): LocalLetterEntry[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    if (!Array.isArray(list)) return [];
    return list.filter(
      (e): e is LocalLetterEntry =>
        e && typeof e.letterId === "string" && typeof e.updatedAt === "number",
    );
  } catch {
    return [];
  }
}

function safeWrite(list: LocalLetterEntry[]): void {
  if (!isBrowser()) return;
  try {
    const trimmed = list.slice(0, MAX_ENTRIES);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    /* quota / private mode — silent */
  }
}

/** 读全部条目，按 updatedAt 降序。 */
export function getAllLetters(): LocalLetterEntry[] {
  return [...safeRead()].sort((a, b) => b.updatedAt - a.updatedAt);
}

/**
 * Upsert 一条记录。
 * - 新记录：补齐默认字段；createdAt 默认当前时间戳。
 * - 已存在：patch 字段覆盖；createdAt 永不重写；updatedAt 自动刷新。
 * - 传入 `undefined` 的字段不会覆盖已存在的值（保护历史元数据）。
 */
export function upsertLetter(
  patch: Partial<LocalLetterEntry> & { letterId: string },
): LocalLetterEntry | null {
  if (!isBrowser()) return null;
  const list = safeRead();
  const now = Date.now();
  const idx = list.findIndex((e) => e.letterId === patch.letterId);

  let merged: LocalLetterEntry;
  if (idx >= 0) {
    const prev = list[idx];
    merged = {
      ...prev,
      // 只合并 patch 中非 undefined 的字段
      ...Object.fromEntries(
        Object.entries(patch).filter(([, v]) => v !== undefined),
      ),
      letterId: prev.letterId,      // 保险
      createdAt: prev.createdAt,    // 不可变
      updatedAt: now,
    } as LocalLetterEntry;
    list[idx] = merged;
  } else {
    merged = {
      letterId: patch.letterId,
      createdAt: patch.createdAt ?? now,
      updatedAt: now,
      roundCount: patch.roundCount ?? 0,
      status: patch.status ?? "active",
      issueSlug: patch.issueSlug,
      mbtiType: patch.mbtiType,
      cardTitle: patch.cardTitle,
    };
    list.unshift(merged);
  }
  safeWrite(list);
  return merged;
}

export function removeLetter(letterId: string): void {
  safeWrite(safeRead().filter((e) => e.letterId !== letterId));
}

export function clearLetters(): void {
  safeWrite([]);
}
