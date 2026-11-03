import { LuMessageSquarePlus, LuTrash2 } from "react-icons/lu";
import type { AnswerContext, Conversation } from "../lib/types";

/** Pick a past conversation or start a new one. Used by Ask the code and Document Q&A. */
export function ConversationBar({ conversations, currentId, onSelect, onNew, onDelete, disabled }: {
  conversations: Conversation[];
  currentId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete?: (id: number) => void;
  disabled?: boolean;
}) {
  const current = conversations.find((c) => c.id === currentId);
  return (
    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
      {conversations.length > 0 && (
        <select className="select" style={{ maxWidth: 320 }} value={currentId ?? ""} disabled={disabled}
          onChange={(e) => e.target.value && onSelect(Number(e.target.value))} aria-label="Conversation">
          {currentId === null && <option value="">New conversation</option>}
          {conversations.map((c) => (
            <option key={c.id} value={c.id}>{c.title}{c.turns ? ` (${c.turns})` : ""}</option>
          ))}
        </select>
      )}
      <button type="button" className="btn btn-sm" onClick={onNew} disabled={disabled}
        title="Start a new conversation. Follow-up questions are read against the current conversation only.">
        <LuMessageSquarePlus size={15} /> New conversation
      </button>
      {onDelete && current && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onDelete(current.id)} disabled={disabled}
          title="Delete this conversation" aria-label="Delete this conversation">
          <LuTrash2 size={14} />
        </button>
      )}
    </div>
  );
}

/** One line under a follow-up question saying how it was understood. */
export function FollowUpNote({ context }: { context?: AnswerContext }) {
  if (!context?.follow_up) return null;
  return (
    <div className="subtle" style={{ fontSize: 12.5, alignSelf: "flex-end", marginTop: -4 }}>
      {context.resolved_question
        ? <>Follow-up, read as: “{context.resolved_question}”</>
        : <>Follow-up, answered with the previous {context.turns_used === 1 ? "question" : `${context.turns_used} questions`} in mind</>}
    </div>
  );
}
