/* ============================================================
   RSS Ingestion Panel
   - Feed status grid (feeds registered in RSS_ENGINE.FEEDS)
   - Article queue with relevance / severity / velocity grades
   - "Run ingestion" triggers real live fetch — no simulation fallback
   - Graded signals propagate to Stage 1 signal list
   ============================================================ */

const RSS_RAG = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };
const RSS_RAG_SOFT = { R: "var(--red-soft)", A: "var(--amber-soft)", G: "var(--green-soft)" };

function RSSPanel({ onSignalsReady, enabledFeedIds, risks, ticker, companyName }) {
  const [ingestState, setIngestState] = useState("idle"); // idle | running | done | error
  const [progress, setProgress]       = useState("");
  const [feedResults, setFeedResults] = useState([]);
  const [expanded, setExpanded]       = useState(new Set());

  const enabledFeeds = enabledFeedIds
    ? RSS_ENGINE.FEEDS.filter(f => enabledFeedIds.includes(f.id))
    : RSS_ENGINE.FEEDS;

  // Auto-start ingestion when the panel mounts
  useEffect(() => { runIngestion(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const allArticles = feedResults.flatMap(r => r.articles);
  const highVel = allArticles.filter(a => a.velocity >= 3).length;
  const medVel  = allArticles.filter(a => a.velocity === 2).length;
  const totalSignals = allArticles.filter(a => a.velocity > 0).length;
  const liveFeedsCount = feedResults.filter(r => r.fetchStatus === "ok").length;
  const failedFeedsCount = feedResults.filter(r => r.fetchStatus === "failed").length;

  async function runIngestion() {
    setIngestState("running");
    setFeedResults([]);
    setProgress("Starting ingestion…");

    try {
      const results = await RSS_ENGINE.ingestAll({
        enabledFeedIds,
        ticker: ticker || "",
        companyName: companyName || "",
        risks: risks || [],
        onProgress: (msg) => setProgress(msg),
      });
      setFeedResults(results);
      setIngestState("done");
      setProgress("");

      // Propagate signals upward
      const signals = RSS_ENGINE.toSignals(results);
      onSignalsReady?.(signals);
    } catch (e) {
      setIngestState("error");
      setProgress(e.message);
    }
  }

  function toggleExpand(feedId) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(feedId) ? next.delete(feedId) : next.add(feedId);
      return next;
    });
  }

  return (
    <div data-screen-label="RSS Ingestion" className="bb-panel">
      <BBTermHeader
        section="RSS SIGNALS"
        title="Industry News · Regulatory · Macro Feeds"
        liveMode={true}
        status={
          ingestState === "running" ? `⟳  ${progress}` :
          ingestState === "done"    ? `${allArticles.length} ARTICLES GRADED  ·  ${totalSignals} VELOCITY SIGNALS  ·  ${liveFeedsCount} LIVE${failedFeedsCount > 0 ? `  ·  ${failedFeedsCount} FAILED` : ""}` :
          "READY — CLICK RUN INGESTION TO GRADE LIVE INDUSTRY SIGNALS"
        }
        actions={
          <button className="btn btn-sm btn-primary" onClick={runIngestion} disabled={ingestState==="running"}>
            {ingestState==="running" ? <><span className="spin" style={{marginRight:5}}/> RUNNING…</> : <><Icon name="satellite" size={12}/> RUN INGESTION</>}
          </button>
        }
      />

      {/* Stat ticker — visible after ingestion */}
      {ingestState === "done" && (
        <div className="bb-stat-ticker">
          <div className="bb-ticker-item"><div className="bb-ticker-label">ARTICLES</div><div className="bb-ticker-val">{allArticles.length}</div></div>
          <div className="bb-ticker-item"><div className="bb-ticker-label">HIGH VEL</div><div className="bb-ticker-val red">{highVel}</div></div>
          <div className="bb-ticker-item"><div className="bb-ticker-label">MED VEL</div><div className="bb-ticker-val amber">{medVel}</div></div>
          <div className="bb-ticker-item"><div className="bb-ticker-label">SIGNALS</div><div className="bb-ticker-val orange">{totalSignals}</div></div>
          <div className="bb-ticker-item"><div className="bb-ticker-label">LIVE FEEDS</div><div className="bb-ticker-val green">{liveFeedsCount}</div></div>
          {failedFeedsCount > 0 && (
            <div className="bb-ticker-item"><div className="bb-ticker-label">FAILED</div><div className="bb-ticker-val red">{failedFeedsCount}</div></div>
          )}
        </div>
      )}

      {/* Feed status grid */}
      <div className="bb-section-sep">
        <span>FEED STATUS</span>
        <span>{enabledFeeds.length} FEEDS ENABLED · LIVE ONLY</span>
      </div>
      <div className="rss-feed-grid">
        {enabledFeeds.map(feed => {
          const result = feedResults.find(r => r.feed.id === feed.id);
          const status = ingestState === "running" ? "pending" : result ? result.fetchStatus : "idle";
          const articles = result?.articles || [];
          const topVel = articles.length ? Math.max(...articles.map(a => a.velocity)) : 0;
          return (
            <div
              key={feed.id}
              className={`rss-feed-card ${result ? "rss-feed-done" : ""}`}
              onClick={() => result && toggleExpand(feed.id)}
              style={{cursor: result ? "pointer" : "default"}}
            >
              <div className="rss-feed-head">
                <Icon name={feed.icon || "wifi"} size={14} className="muted"/>
                <div className="rss-feed-name">{feed.name}</div>
                <FeedStatusBadge status={status}/>
              </div>
              <div className="rss-feed-meta">
                <span className="mono">{feed.domains.join(", ")}</span>
                {result && result.fetchStatus === "ok" && (
                  <span className="mono" style={{color: topVel >= 3 ? RSS_RAG.R : topVel >= 2 ? RSS_RAG.A : "var(--ink-3)"}}>
                    {articles.length} articles · max v={topVel >= 0 ? "+" : ""}{topVel}
                  </span>
                )}
                {result && result.fetchStatus === "failed" && (
                  <span className="mono" style={{color: "var(--red-ink)", fontSize: 10}}>Unreachable — check proxy</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Article queue — expanded feed */}
      {feedResults.map(result => {
        if (!expanded.has(result.feed.id)) return null;
        return (
          <div key={result.feed.id} className="rss-article-list">
            <div className="rss-article-list-head">
              <div style={{fontWeight:500, fontSize:12.5}}>{result.feed.name}</div>
              <div className="mono" style={{fontSize:10.5, color:"var(--ink-3)"}}>Live fetch</div>
              <button className="btn btn-sm btn-ghost" onClick={() => toggleExpand(result.feed.id)} style={{marginLeft:"auto"}}>
                <Icon name="x" size={12}/>
              </button>
            </div>
            {result.fetchStatus === "failed" ? (
              <div style={{padding:"14px 4px", color:"var(--red-ink)", fontSize:11.5}}>
                Feed unreachable — the proxy request to {result.feed.name} failed or timed out. Retry via RUN INGESTION, or check the rss-proxy route if this persists.
              </div>
            ) : result.articles.length === 0 ? (
              <div style={{padding:"14px 4px", color:"var(--ink-3)", fontSize:11.5}}>
                Feed reached successfully, but no articles matched this feed's relevance filters{result.feed.companyGated ? " (company-gated — nothing mentioned the active company)" : ""}.
              </div>
            ) : (
              result.articles.map(a => <ArticleRow key={a.id} article={a}/>)
            )}
          </div>
        );
      })}

      {/* Flat article queue when nothing expanded */}
      {ingestState === "done" && expanded.size === 0 && allArticles.length > 0 && (
        <div>
          <div className="bb-section-sep">
            <span>ARTICLE QUEUE</span>
            <span>TOP 15 BY VELOCITY · CLICK FEED CARD TO EXPAND</span>
          </div>
          <div className="rss-article-list">
            <div className="rss-art-thead">
              <div className="rss-art-th">RAG</div>
              <div className="rss-art-th rss-art-th-title">Title</div>
              <div className="rss-art-th">Feed</div>
              <div className="rss-art-th">Vel</div>
              <div className="rss-art-th">Rel</div>
              <div className="rss-art-th">Sev</div>
              <div className="rss-art-th">Risks</div>
            </div>
            {[...allArticles]
              .sort((a, b) => b.velocity - a.velocity)
              .slice(0, 15)
              .map(a => <ArticleRow key={a.id} article={a} compact />)
            }
          </div>
        </div>
      )}

      {/* Empty state after failed ingestion */}
      {ingestState === "done" && allArticles.length === 0 && (
        <div style={{padding:"24px 0", textAlign:"center", color:"var(--ink-3)", fontSize:11.5}}>
          No articles retrieved — all feeds failed to fetch. Check the rss-proxy is running and the feed URLs are reachable.
        </div>
      )}

      {/* Grading methodology note */}
      <div style={{flex:1}}/>
      <div className="rss-method-note">
        <div className="mono" style={{fontSize:10.5, fontWeight:500, marginBottom:5}}>GRADING METHODOLOGY</div>
        <div style={{display:"grid", gridTemplateColumns:"repeat(3, 1fr)", gap:"6px 14px", fontSize:11, color:"var(--ink-2)", lineHeight:1.5}}>
          <div><b style={{fontWeight:500}}>Relevance</b> — keyword density against 8 risk-domain vocabularies. 3+ hits → score 1.0.</div>
          <div><b style={{fontWeight:500}}>Severity</b> — presence of urgency words (critical/mandatory/violation/etc.) scaled by weight.</div>
          <div><b style={{fontWeight:500}}>Velocity</b> — round(relevance × severity × 5 × feed_weight). Propagates to risk register delta.</div>
        </div>
      </div>
    </div>
  );
}

function FeedStatusBadge({ status }) {
  if (status === "idle")    return <span className="rss-badge rss-badge-idle mono">IDLE</span>;
  if (status === "pending") return <span className="rss-badge rss-badge-pending mono"><span className="spin" style={{marginRight:4}}/>FETCHING</span>;
  if (status === "ok")      return <span className="rss-badge rss-badge-ok mono">LIVE</span>;
  if (status === "failed")  return <span className="rss-badge rss-badge-sim mono" style={{color:"var(--red-ink)"}}>FAILED</span>;
  return null;
}

function RssStat({ l, v, color }) {
  return (
    <div className="rss-stat">
      <div className="l">{l}</div>
      <div className="v mono" style={color ? { color } : null}>{v}</div>
    </div>
  );
}

function ArticleRow({ article: a, compact }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`rss-art-row rss-art-row-${a.rag.toLowerCase()}${open ? " open" : ""}`}
      onClick={() => !compact && setOpen(o => !o)}>
      <div className="rss-art-rag">
        <RAGChip rag={a.rag}>{a.rag}</RAGChip>
      </div>
      <div className="rss-art-title">
        <div className="rss-art-title-text">{a.title}</div>
        {!compact && (
          <div className="rss-art-meta mono">
            {a.feedName} · {new Date(a.pubDate).toLocaleDateString("en-US",{month:"short",day:"numeric"})}
          </div>
        )}
      </div>
      {compact && <div className="rss-art-feed mono" style={{fontSize:10}}>{a.feedName.split(" ")[0]}</div>}
      <div className="rss-art-score">
        <VelocityPill v={a.velocity}/>
      </div>
      <div className="rss-art-score mono" style={{color: "var(--ink-3)"}}>
        {(a.relevance * 100).toFixed(0)}%
      </div>
      <div className="rss-art-score mono" style={{color: "var(--ink-3)"}}>
        {(a.severity * 100).toFixed(0)}%
      </div>
      <div className="rss-art-risks">
        {a.affectedRisks.map(r => (
          <span key={r} className="mono" style={{fontSize:10,padding:"1px 5px",border:"1px solid var(--line)",borderRadius:3,color:"var(--ink-2)"}}>{r}</span>
        ))}
      </div>
      {open && !compact && (
        <div className="rss-art-detail">
          <div style={{display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:"6px 12px", fontSize:11, color:"var(--ink-2)"}}>
            <div><span className="mono" style={{color:"var(--ink-3)"}}>Source</span><br/>{a.feedName}</div>
            <div><span className="mono" style={{color:"var(--ink-3)"}}>Published</span><br/>{new Date(a.pubDate).toLocaleString("en-US",{dateStyle:"medium",timeStyle:"short"})}</div>
            <div><span className="mono" style={{color:"var(--ink-3)"}}>Domains</span><br/>{a.domains.join(", ")}</div>
            <div><span className="mono" style={{color:"var(--ink-3)"}}>Relevance score</span><br/><span className="mono">{(a.relevance * 100).toFixed(0)}%</span></div>
            <div><span className="mono" style={{color:"var(--ink-3)"}}>Severity score</span><br/><span className="mono">{(a.severity * 100).toFixed(0)}%</span></div>
            <div><span className="mono" style={{color:"var(--ink-3)"}}>Velocity delta</span><br/><VelocityPill v={a.velocity}/></div>
          </div>
          {a.url && (
            <div style={{marginTop:8}}>
              <a href={a.url} target="_blank" rel="noopener noreferrer"
                className="mono" style={{fontSize:11, color:"var(--acc-ink)", textDecoration:"none"}}>
                View source →
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

window.RSSPanel = RSSPanel;
