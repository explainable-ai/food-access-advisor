import { useState } from "react";

type ChatBarProps = {
  placeholder: string;
  onAsk: (question: string) => Promise<string>;
};

/**
 * Bottom-anchored question input shared by both Advisor workspaces.
 * Which endpoint it hits is decided by the page it's rendered on (passed
 * in as `onAsk`), not by any client-side intent classification -- the
 * same deterministic-routing principle as orchestration.route_request.
 */
export function ChatBar({ placeholder, onAsk }: ChatBarProps) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim() || loading) return;
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      const result = await onAsk(question.trim());
      setAnswer(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-bar-wrapper">
      {loading && (
        <p className="chat-status">
          Asking the advisor -- this runs a full tool-calling pass plus a Bedrock call, so it can take up to a
          minute or more.
        </p>
      )}
      {error && <p className="chat-error">{error}</p>}
      {answer && (
        <div className="evidence-panel">
          <p className="evidence-panel-label">Advisor's answer</p>
          <p className="evidence-panel-text">{answer}</p>
        </div>
      )}
      <form className="chat-bar" onSubmit={handleSubmit}>
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={placeholder}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !question.trim()}>
          {loading ? "Asking..." : "Ask"}
        </button>
      </form>
    </div>
  );
}
