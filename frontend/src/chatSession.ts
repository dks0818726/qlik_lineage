import { useSyncExternalStore } from "react";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  trace?: any[];
}

interface ChatState {
  messages: ChatMessage[];
  /** True while a question is in flight, so navigating away and back keeps the spinner. */
  busy: boolean;
  /** A half-typed question, kept so switching tabs mid-sentence does not lose it. */
  draft: string;
}

/** Per-tab only: sessionStorage is cleared when the tab closes, and is not shared
 * with other tabs or persisted to disk long-term. Chat history is deliberately not
 * sent anywhere or stored server-side. */
const STORAGE_KEY = "qlik-lineage.chat.messages";

function loadMessages(): ChatMessage[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveMessages(messages: ChatMessage[]): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  } catch {
    // Quota exceeded (large tool traces) — history stays in memory for this page view.
  }
}

// The state lives outside React so that unmounting ChatPage on a tab change does
// not discard it. Previously `messages` was component state, so switching to
// Graph/Impact and back reset the conversation.
let state: ChatState = { messages: loadMessages(), busy: false, draft: "" };

const listeners = new Set<() => void>();

function emit(next: ChatState): void {
  state = next;
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): ChatState {
  return state;
}

export function useChatSession(): ChatState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

function appendMessage(message: ChatMessage): void {
  const messages = [...state.messages, message];
  saveMessages(messages);
  emit({ ...state, messages });
}

export function setDraft(draft: string): void {
  emit({ ...state, draft });
}

export function clearChat(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  emit({ messages: [], busy: false, draft: "" });
}

/** Send a question. Lives in the store rather than the component so that an
 * in-flight answer still lands if the user switches tabs while waiting. */
export async function askQuestion(
  question: string,
  send: (q: string) => Promise<any>,
): Promise<void> {
  const trimmed = question.trim();
  if (!trimmed || state.busy) return;

  appendMessage({ role: "user", content: trimmed });
  emit({ ...state, busy: true, draft: "" });

  try {
    const r = await send(trimmed);
    appendMessage({
      role: "assistant",
      content: r.answer || "(no answer)",
      trace: r.trace || [],
    });
  } catch (e: any) {
    appendMessage({ role: "assistant", content: `Error: ${e}` });
  } finally {
    emit({ ...state, busy: false });
  }
}
