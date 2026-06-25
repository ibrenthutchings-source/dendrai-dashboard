/* ============================================================
   AiChatPanel — slide-out conversational AI panel
   Supports Claude (streaming + MCP tool use) and Gemini (streaming).
   ============================================================ */

const TOOL_LABELS = {
  get_financials:   "Fetching EDGAR financials",
  get_risk_factors: "Reading 10-K risk factors",
  get_8k_events:    "Scanning 8-K events",
  get_peers:        "Benchmarking peers",
  get_industry_news:"Ingesting industry signals",
  run_quant_models: "Running quant models",
};

function ProviderIcon({ provider }) {
  const isGemini = provider === "gemini";
  return (
    <div className="ai-chat-provider-icon" style={isGemini ? {background:"var(--surface-2)", border:"1px solid var(--line)"} : {}}>
      {isGemini
        ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 2L12 22M2 12L22 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"/><path d="M12 2C12 2 17 7 17 12C17 17 12 22 12 22C12 22 7 17 7 12C7 7 12 2 12 2Z" stroke="currentColor" strokeWidth="2" fill="none"/></svg>
        : <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm-1-5h2v2h-2zm0-8h2v6h-2z"/></svg>
      }
    </div>
  );
}

function ChatMessage({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div className={"ai-chat-msg" + (isUser ? " user" : " assistant") + (msg.error ? " error" : "")}>
      {!isUser && msg.toolTrace && msg.toolTrace.length > 0 && (
        <div className="ai-chat-tool-trace">
          {msg.toolTrace.map((t, i) => (
            <div key={i} className="ai-chat-tool-step">
              <Icon name={t.done ? (t.error ? "x" : "check") : "gear"} size={9} />
              <span>{TOOL_LABELS[t.tool] || t.tool}</span>
              {t.done && !t.error && <span className="ai-chat-tool-ok">✓</span>}
            </div>
          ))}
        </div>
      )}
      <div className="ai-chat-msg-text">
        {msg.text}
        {msg.loading && !msg.toolTrace?.some(t => !t.done) && (
          <span className="ai-chat-cursor" />
        )}
      </div>
    </div>
  );
}

function AiChatPanel({ open, onClose, provider = "claude", buttonLabel, ticker, industry, output }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const abortRef = useRef(null);

  // Scroll to bottom on new content
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  }, [input]);

  // Abort any in-flight request when panel closes
  useEffect(() => {
    if (!open && abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, [open]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");

    const userMsg = { role: "user", text };
    const assistantMsg = { role: "assistant", text: "", loading: true, toolTrace: [] };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setLoading(true);

    // Build history for the request (exclude the placeholder we just added)
    const history = messages.map(m => ({ role: m.role, content: m.text }));

    // Collect risks from the output prop if available
    const risks = output?.s2?.risks || [];
    const loopStats = output?.s6?.loop || {};
    const geminiApiKey = localStorage.getItem("dendrai_gemini_api_key") || "";

    const abort = new AbortController();
    abortRef.current = abort;

    try {
      const res = await fetch("/api/mcp/ai/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abort.signal,
        body: JSON.stringify({
          message: text,
          history,
          ticker: ticker || "",
          industry: industry || "",
          provider,
          gemini_api_key: geminiApiKey,
          risks,
          loop_stats: loopStats,
        }),
      });

      if (!res.ok) {
        let errMsg = `Server error ${res.status}`;
        try { const j = await res.json(); errMsg = j.detail || errMsg; } catch {}
        setMessages(prev => {
          const msgs = [...prev];
          msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], text: errMsg, loading: false, error: true };
          return msgs;
        });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let accText = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.type === "text_delta") {
            accText += evt.delta || "";
            setMessages(prev => {
              const msgs = [...prev];
              const last = msgs[msgs.length - 1];
              msgs[msgs.length - 1] = { ...last, text: accText, loading: true };
              return msgs;
            });
          } else if (evt.type === "tool_call") {
            setMessages(prev => {
              const msgs = [...prev];
              const last = msgs[msgs.length - 1];
              const trace = [...(last.toolTrace || []), { tool: evt.tool, done: false }];
              msgs[msgs.length - 1] = { ...last, toolTrace: trace, loading: true };
              return msgs;
            });
          } else if (evt.type === "tool_result") {
            setMessages(prev => {
              const msgs = [...prev];
              const last = msgs[msgs.length - 1];
              const trace = (last.toolTrace || []).map(t =>
                t.tool === evt.tool && !t.done ? { ...t, done: true, error: evt.is_error } : t
              );
              msgs[msgs.length - 1] = { ...last, toolTrace: trace, loading: true };
              return msgs;
            });
          } else if (evt.type === "done") {
            const finalText = evt.final_text || accText;
            setMessages(prev => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], text: finalText, loading: false };
              return msgs;
            });
          } else if (evt.type === "error") {
            setMessages(prev => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], text: evt.message || "Unknown error", loading: false, error: true };
              return msgs;
            });
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") return;
      setMessages(prev => {
        const msgs = [...prev];
        const last = msgs[msgs.length - 1];
        if (last?.loading) {
          msgs[msgs.length - 1] = { ...last, text: e.message || "Request failed", loading: false, error: true };
        }
        return msgs;
      });
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  const clearChat = () => { setMessages([]); setInput(""); };

  const providerName = provider === "gemini" ? "Gemini" : "Claude";
  const entityLabel = ticker ? ` · ${ticker.toUpperCase()}` : "";

  return (
    <div className={"ai-chat-panel" + (open ? " open" : "")} role="complementary" aria-label="AI Chat">
      {/* Header */}
      <div className="ai-chat-header">
        <div className="ai-chat-header-info">
          <ProviderIcon provider={provider} />
          <div>
            <div className="ai-chat-title">{providerName}{entityLabel}</div>
            <div className="ai-chat-sub">Natural language queries · MCP data access</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {messages.length > 0 && (
            <button className="ai-chat-icon-btn" title="Clear chat" onClick={clearChat} disabled={loading}>
              <Icon name="reset" size={12} />
            </button>
          )}
          <button className="ai-chat-icon-btn" title="Close" onClick={onClose}>
            <Icon name="x" size={12} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="ai-chat-messages">
        {messages.length === 0 ? (
          <div className="ai-chat-empty">
            <div className="ai-chat-empty-glyph">
              {provider === "gemini" ? "✦" : "◆"}
            </div>
            <div className="ai-chat-empty-title">Ask {providerName} about your data</div>
            <div className="ai-chat-empty-sub">
              Query financials, risk factors, macro indicators, and more in plain language.
              {provider === "claude" && " Claude can fetch live data from EDGAR, FRED, and RSS feeds."}
            </div>
            <div className="ai-chat-suggestions">
              {[
                ticker ? `What are the key risk factors for ${ticker}?` : "What is the risk profile of this entity?",
                "Summarize the top 3 risks from the register",
                ticker ? `How does ${ticker} compare to peers on margins?` : "What macro indicators are most relevant?",
              ].map((s, i) => (
                <button key={i} className="ai-chat-suggestion"
                  onClick={() => { setInput(s); textareaRef.current?.focus(); }}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, i) => <ChatMessage key={i} msg={msg} />)
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="ai-chat-input-area">
        <textarea
          ref={textareaRef}
          className="ai-chat-textarea"
          value={input}
          rows={1}
          disabled={loading}
          placeholder={`Ask ${providerName} anything about ${ticker || "the data"}…`}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          className="ai-chat-send"
          onClick={send}
          disabled={!input.trim() || loading}
          title="Send (Enter)"
        >
          {loading
            ? <span className="spin" style={{ width: 13, height: 13, borderWidth: 2, borderColor: "rgba(255,255,255,0.4)", borderTopColor: "#fff" }} />
            : <Icon name="spark" size={13} />
          }
        </button>
      </div>
    </div>
  );
}

Object.assign(window, { AiChatPanel });
