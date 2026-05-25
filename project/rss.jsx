/* ============================================================
   RSS Ingestion Panel
   - Feed status grid (6 registered feeds)
   - Article queue with relevance / severity / velocity grades
   - "Run ingestion" triggers real fetch + fallback simulation
   - Graded signals propagate to Stage 1 signal list
   ============================================================ */

const RSS_RAG = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };
const RSS_RAG_SOFT = { R: "var(--red-soft)", A: "var(--amber-soft)", G: "var(--green-soft)" };

function RSSPanel({ onSignalsReady }) {
  const [ingestState, setIngestState] = useState("idle"); // idle | running | done | error
  const [progress, setProgress]       = useState("");
  const [feedResults, setFeedResults] = useState([]);
  const [expanded, setExpanded]       = useState(new Set());
  const [simMode, setSimMode]         = useState(false);

  const allArticles = feedResults.flatMap(r => r.articles);
  const highVel = allArticles.filter(a => a.velocity >= 3).length;
  const medVel  = allArticles.filter(a => a.velocity === 2).length;
  const totalSignals = allArticles.filter(a => a.velocity > 0).length;

  async function runIngestion() {
    setIngestState("running");
    setFeedResults([]);
    setProgress("Starting ingestion…");

    try {
      const results = await RSS_ENGINE.ingestAll({
        simulate: simMode,
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
    <div data-screen-label="RSS Ingestion">
      <div className="panel-head">
        <div>
          <div className="kicker">Signal intake · RSS grading</div>
          <div className="panel-title mt-8">Industry news · regulatory advisories · macro feeds</div>
          <div className="panel-sub">
            {ingestState === "running" ? progress :
             ingestState === "done"    ? `${allArticles.length} articles graded · ${totalSignals} velocity signals` :
             "Click Run Ingestion to fetch live feeds or simulate. Graded articles propagate to Stage 1 signals."}
          </div>
        </div>
        <div style={{display:"flex", gap:8, alignItems:"center"}}>
          <label style={{display:"flex", alignItems:"center", gap:6, fontSize:11.5, color:"var(--ink-2)", cursor:"pointer", userSelect:"none"}}>
            <input type="checkbox" checked={simMode} onChange={e => setSimMode(e.target.checked)}
              style={{width:13,height:13,cursor:"pointer"}}/>
            Simulate only
          </label>
          <button className="btn btn-sm btn-primary" onClick={runIngestion} disabled={ingestState === "running"}>
            {ingestState === "running" ? <><span className="spin" style={{marginRight:5}}/> Running…</> : <><Icon name="satellite" size={12}/> Run Ingestion</>}
          </button>
        </div>
      </div>

      {/* Feed status grid */}
      <div className="rss-feed-grid">
        {RSS_ENGINE.FEEDS.map(feed => {
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
                {result && (
                  <span className="mono" style={{color: topVel >= 3 ? RSS_RAG.R : topVel >= 2 ? RSS_RAG.A : "var(--ink-3)"}}>
                    {articles.length} articles · max v={topVel >= 0 ? "+" : ""}{topVel}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Summary stats when done */}
      {ingestState === "done" && (
        <div className="rss-stat-row">
          <RssStat l="Total articles" v={allArticles.length}/>
          <RssStat l="High velocity (≥3)" v={highVel} color="var(--red-ink)"/>
          <RssStat l="Medium velocity (2)" v={medVel} color="var(--amber-ink)"/>
          <RssStat l="Signals propagated" v={totalSignals} color="var(--acc-ink)"/>
          <RssStat l="Feeds ingested" v={feedResults.filter(r => r.fetchStatus === "ok").length + " live"}/>
          <RssStat l="Simulated/fallback" v={feedResults.filter(r => r.fetchStatus !== "ok").length}/>
        </div>
      )}

      {/* Article queue — expanded feed */}
      {feedResults.map(result => {
        if (!expanded.has(result.feed.id)) return null;
        return (
          <div key={result.feed.id} className="rss-article-list">
            <div className="rss-article-list-head">
              <div style={{fontWeight:500, fontSize:12.5}}>{result.feed.name}</div>
              <div className="mono" style={{fontSize:10.5, color:"var(--ink-3)"}}>
                {result.fetchStatus === "ok" ? "Live fetch" : result.fetchStatus === "simulated" ? "Simulated" : "Fallback simulation"}
              </div>
              <button className="btn btn-sm btn-ghost" onClick={() => toggleExpand(result.feed.id)} style={{marginLeft:"auto"}}>
                <Icon name="x" size={12}/>
              </button>
            </div>
            {result.articles.map(a => <ArticleRow key={a.id} article={a}/>)}
          </div>
        );
      })}

      {/* Flat article queue when nothing expanded */}
      {ingestState === "done" && expanded.size === 0 && (
        <div>
          <SectionLabel right={<span className="mono muted" style={{fontSize:10.5}}>Click a feed card to expand · showing top 15 by velocity</span>}>
            Article queue
          </SectionLabel>
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

      {/* Grading methodology note */}
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
  if (status === "idle")      return <span className="rss-badge rss-badge-idle mono">IDLE</span>;
  if (status === "pending")   return <span className="rss-badge rss-badge-pending mono"><span className="spin" style={{marginRight:4}}/>FETCHING</span>;
  if (status === "ok")        return <span className="rss-badge rss-badge-ok mono">LIVE</span>;
  if (status === "fallback")  return <span className="rss-badge rss-badge-sim mono">FALLBACK</span>;
  if (status === "simulated") return <span className="rss-badge rss-badge-sim mono">SIMULATED</span>;
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
