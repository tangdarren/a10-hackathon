import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  fetchCaller,
  fetchOutreach,
  fetchWorkOrder,
  runCaller,
  runOutreach,
  runScout,
  simulateCall,
  stopCaller,
  stopOutreach,
} from './api';
import {
  AGENT_PROGRESS,
  INITIAL_AGENTS,
  type Agent,
  type AgentId,
  type AgentStatus,
  type CallerResult,
  type OutreachResult,
  type Quote,
  type ScoutResult,
} from './agents';
import AppShell from './AppShell';
// The ElevenLabs SDK is large; load it only when a call is joined.
const CallModal = lazy(() => import('./CallModal'));
import type { WorkOrder } from './types';
import './App.css';

function formatTime(value: string | Date) {
  const date = typeof value === 'string' ? new Date(value) : value;
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

const TRADE_SEARCH: Record<string, string> = {
  plumbing: 'plumbers',
  hvac: 'HVAC contractors',
  appliance: 'appliance repair',
  locksmith: 'locksmiths',
  electrical: 'electricians',
  general: 'handyman',
};

function mapsVerifyUrl(trades: string[], building: string) {
  const primary = trades[0] ?? 'handyman';
  const query = `${TRADE_SEARCH[primary] ?? primary} near ${building}`;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
}

const AGENT_IDS: AgentId[] = ['scout', 'outreach', 'caller'];

const baseAgent = (id: AgentId) =>
  INITIAL_AGENTS.find((agent) => agent.id === id)!;

const freshStatuses = (): Record<AgentId, AgentStatus> => ({
  scout: 'waiting',
  outreach: 'waiting',
  caller: 'waiting',
});

function quoteStatusLabel(quote: Quote) {
  switch (quote.reply.status) {
    case 'quoted':
      return `$${quote.reply.price} · ${quote.reply.window}`;
    case 'declined':
      return 'Declined';
    case 'waiting':
      return 'Waiting…';
    default:
      return 'No reply';
  }
}

function ResultsModal({
  eyebrow,
  title,
  onClose,
  headerExtra,
  className,
  children,
}: {
  eyebrow: string;
  title: string;
  onClose: () => void;
  headerExtra?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className="qr-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`${eyebrow} results`}
      onClick={onClose}
    >
      <div
        className={className ? `results-modal ${className}` : 'results-modal'}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="results-modal-header">
          <div>
            <p className="results-eyebrow">{eyebrow}</p>
            <h2>{title}</h2>
            {headerExtra}
          </div>
          <button className="reset-btn" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export default function DispatchBoard() {
  const { orderId } = useParams<{ orderId: string }>();
  const [order, setOrder] = useState<WorkOrder | null>(null);
  const [error, setError] = useState('');
  const [agents, setAgents] = useState<Agent[]>(() =>
    INITIAL_AGENTS.map((agent) => ({ ...agent })),
  );
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [scout, setScout] = useState<ScoutResult | null>(null);
  const [outreach, setOutreach] = useState<OutreachResult | null>(null);
  const [caller, setCaller] = useState<CallerResult | null>(null);
  const [openPanel, setOpenPanel] = useState<AgentId | null>(null);
  const [showLiveView, setShowLiveView] = useState(false);
  const [joinedCall, setJoinedCall] = useState<number | null>(null);

  const timersRef = useRef<Partial<Record<AgentId, number>>>({});
  const statusRef = useRef<Record<AgentId, AgentStatus>>(freshStatuses());
  const abortRef = useRef<Partial<Record<AgentId, AbortController>>>({});
  const runIdRef = useRef<Record<AgentId, number>>({
    scout: 0,
    outreach: 0,
    caller: 0,
  });

  const clearAgentTimer = (id: AgentId) => {
    const timer = timersRef.current[id];
    if (timer !== undefined) {
      window.clearTimeout(timer);
      delete timersRef.current[id];
    }
  };

  /** Invalidate any in-flight run for every agent. */
  const cancelAll = () => {
    AGENT_IDS.forEach((id) => {
      clearAgentTimer(id);
      runIdRef.current[id] += 1;
      abortRef.current[id]?.abort();
      delete abortRef.current[id];
    });
  };

  const resetResults = () => {
    setScout(null);
    setOutreach(null);
    setCaller(null);
    setOpenPanel(null);
  };

  useEffect(() => cancelAll, []);

  useEffect(() => {
    if (!orderId) return;

    let cancelled = false;
    cancelAll();
    resetResults();
    setPipelineRunning(false);
    statusRef.current = freshStatuses();
    setAgents(INITIAL_AGENTS.map((agent) => ({ ...agent })));
    setError('');
    setOrder(null);

    void fetchWorkOrder(orderId)
      .then((next) => {
        if (!cancelled) setOrder(next);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Order not found');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [orderId]);

  const updateAgent = (id: AgentId, patch: Partial<Agent>) => {
    if (patch.status) statusRef.current[id] = patch.status;
    setAgents((current) =>
      current.map((agent) => (agent.id === id ? { ...agent, ...patch } : agent)),
    );
  };

  const isCurrent = (id: AgentId, runId: number) =>
    runIdRef.current[id] === runId && statusRef.current[id] === 'running';

  /** Cycle status verbs while an agent runs. Safe to stop via clearAgentTimer. */
  const playProgress = (id: AgentId, runId: number) => {
    const phases = AGENT_PROGRESS[id];
    let index = 0;

    const tick = () => {
      if (!isCurrent(id, runId) || index >= phases.length) return;
      updateAgent(id, { action: phases[index] });
      index += 1;
      // Loop the last verb until the real call finishes (Scout can take several seconds).
      if (index >= phases.length) index = phases.length - 1;
      timersRef.current[id] = window.setTimeout(tick, 850);
    };

    tick();
  };

  /**
   * Run one agent's backend call with progress animation.
   * Resolves to null if the agent was stopped, superseded, or failed.
   */
  const runAgent = async <T,>(
    id: AgentId,
    call: (signal: AbortSignal, runId: number) => Promise<T>,
    { animate = true }: { animate?: boolean } = {},
  ): Promise<T | null> => {
    const runId = ++runIdRef.current[id];
    abortRef.current[id]?.abort();
    const controller = new AbortController();
    abortRef.current[id] = controller;
    clearAgentTimer(id);

    updateAgent(id, {
      status: 'running',
      action: 'Starting…',
      meta: baseAgent(id).permission,
    });
    if (animate) playProgress(id, runId);

    try {
      const result = await call(controller.signal, runId);
      return isCurrent(id, runId) ? result : null;
    } catch (err) {
      if (controller.signal.aborted || runIdRef.current[id] !== runId) return null;
      updateAgent(id, {
        status: 'error',
        action: 'Failed',
        meta: err instanceof Error ? err.message : `${baseAgent(id).name} failed`,
      });
      return null;
    } finally {
      clearAgentTimer(id);
    }
  };

  const pollOutreach = async (
    id: string,
    signal: AbortSignal,
    runId: number,
  ): Promise<OutreachResult> => {
    let snap = await runOutreach(id, { signal });
    for (;;) {
      if (!isCurrent('outreach', runId)) return snap;
      setOutreach(snap);
      const sent = snap.emails.filter((email) => email.status === 'sent').length;
      const replied = snap.quotes.filter(
        (quote) => quote.reply.status !== 'waiting',
      ).length;
      updateAgent('outreach', {
        action: snap.steps[snap.steps.length - 1] ?? 'Starting…',
        meta: snap.awaiting_replies
          ? `${replied} of ${snap.quotes.length} replied · listening`
          : `${sent} of ${snap.emails.length} sent · ${
              snap.send_mode === 'browserbase' ? `from ${snap.from_email}` : 'simulated'
            }`,
      });
      if (snap.status === 'done') return snap;
      if (snap.status === 'error') throw new Error(snap.summary || 'Outreach failed');
      if (snap.status === 'stopped') throw new Error('Stopped');
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      snap = await fetchOutreach(id, { signal });
    }
  };

  const pollCaller = async (
    id: string,
    signal: AbortSignal,
    runId: number,
  ): Promise<CallerResult> => {
    let snap = await runCaller(id, { signal });
    for (;;) {
      if (!isCurrent('caller', runId)) return snap;
      setCaller(snap);
      const calls = snap.calls ?? [];
      const total = calls.length;
      const current = snap.current ?? null;
      const line =
        current !== null && current >= 0 && current < calls.length
          ? calls[current]
          : null;
      updateAgent('caller', {
        action: snap.steps[snap.steps.length - 1] ?? 'Starting…',
        meta: line
          ? `Call ${current! + 1} of ${total} · ${
              line.mode === 'voice' ? 'live voice' : 'simulated'
            }`
          : `${snap.called_count ?? 0} of ${total} called`,
      });
      if (snap.status === 'done') return snap;
      if (snap.status === 'error') throw new Error(snap.summary || 'Caller failed');
      if (snap.status === 'stopped') throw new Error('Stopped');
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      snap = await fetchCaller(id, { signal });
    }
  };

  const runPipeline = async () => {
    if (!orderId) return;

    cancelAll();
    resetResults();
    AGENT_IDS.forEach((id) =>
      updateAgent(id, {
        status: 'waiting',
        action: 'Waiting',
        meta: baseAgent(id).permission,
      }),
    );

    // Scout → find fixers
    const scoutResult = await runAgent(
      'scout',
      (signal) => runScout(orderId, { signal }),
      { animate: true },
    );
    if (!scoutResult) return;
    setScout(scoutResult);
    const fixerCount = scoutResult.fixers.length;
    updateAgent('scout', {
      status: 'done',
      action: 'Done',
      meta: fixerCount === 1 ? '1 fixer found' : `${fixerCount} fixers found`,
    });
    if (!fixerCount) {
      updateAgent('outreach', {
        status: 'waiting',
        action: 'Nobody to contact',
      });
      updateAgent('caller', { status: 'waiting', action: 'Waiting' });
      return;
    }

    // Outreach → email for quotes (background job on the server; poll it)
    const outreachResult = await runAgent(
      'outreach',
      (signal, runId) => pollOutreach(orderId, signal, runId),
      { animate: false },
    );
    if (!outreachResult) return;
    setOutreach(outreachResult);
    const best = outreachResult.quotes.find(
      (quote) => quote.fixer_id === outreachResult.best_fixer_id,
    );
    updateAgent('outreach', {
      status: 'done',
      action: best && best.reply.status === 'quoted'
        ? `Best: ${best.name} · $${best.reply.price}`
        : 'No quotes back',
      meta: `${outreachResult.quoted_count} of ${outreachResult.quotes.length} quoted`,
    });
    if (!outreachResult.quoted_count) {
      updateAgent('caller', {
        status: 'waiting',
        action: 'Nobody replied',
      });
      return;
    }

    // Caller → phone everyone who quoted, then book the best
    const callerResult = await runAgent(
      'caller',
      (signal, runId) => pollCaller(orderId, signal, runId),
      { animate: false },
    );
    if (!callerResult) return;
    setCaller(callerResult);
    const booking = callerResult.booking;
    updateAgent('caller', {
      status: 'done',
      action: booking
        ? `Booked ${booking.name}`
        : callerResult.called_count
          ? `Called ${callerResult.called_count}`
          : 'No call placed',
      meta: booking
        ? `${callerResult.called_count} called · ${booking.window}`
        : '',
    });
  };

  const dispatchTeam = () => {
    if (!order || pipelineRunning) return;
    setPipelineRunning(true);
    void runPipeline().finally(() => setPipelineRunning(false));
  };

  const stopAgent = (id: AgentId) => {
    if (statusRef.current[id] !== 'running') return;
    clearAgentTimer(id);
    runIdRef.current[id] += 1;
    abortRef.current[id]?.abort();
    if (id === 'caller' && orderId) {
      void stopCaller(orderId)
        .then((snap) => setCaller(snap))
        .catch(() => undefined);
    }
    if (id === 'outreach' && orderId) {
      // Releases the Browserbase session, killing the browser mid-send.
      void stopOutreach(orderId)
        .then((snap) => setOutreach(snap))
        .catch(() => undefined);
    }
    updateAgent(id, {
      status: 'stopped',
      action: 'Stopped',
      meta: baseAgent(id).permission,
    });
  };

  const hasResults = (id: AgentId) => {
    if (id === 'scout') return !!scout?.fixers.length;
    if (id === 'outreach') return !!outreach?.quotes.length;
    return !!caller;
  };

  const booked = !!caller?.booking;
  const dispatchLabel = pipelineRunning
    ? 'Dispatching…'
    : booked
      ? 'Booked'
      : 'Dispatch';

  const outreachRunning =
    agents.find((agent) => agent.id === 'outreach')?.status === 'running';
  const liveViewUrl = outreachRunning ? outreach?.live_view_url ?? null : null;
  const callerRunning =
    agents.find((agent) => agent.id === 'caller')?.status === 'running';
  const callIndex = callerRunning ? caller?.current ?? null : null;
  const waitingCall =
    callIndex !== null && caller
      ? caller.calls[callIndex]?.status === 'ready' ||
        caller.calls[callIndex]?.status === 'in_call'
        ? caller.calls[callIndex]
        : null
      : null;
  const liveVoice = caller?.voice_mode === 'elevenlabs';

  // Auto-open the ElevenLabs call when Caller is ready for you to play the plumber.
  useEffect(() => {
    if (!liveVoice || callIndex === null || !waitingCall) return;
    if (waitingCall.status !== 'ready') return;
    if (joinedCall === callIndex) return;
    setJoinedCall(callIndex);
  }, [liveVoice, callIndex, waitingCall, joinedCall]);

  return (
    <AppShell>
      <main className="content">
        <div className="detail-nav">
          <Link to="/" className="back-link">
            ← All orders
          </Link>
        </div>

        <section className="work-order" aria-label="Work order">
          {error ? (
            <div className="work-order-empty">
              <div>
                <h2>Order unavailable</h2>
                <p className="work-order-location">{error}</p>
              </div>
            </div>
          ) : order ? (
            <>
              <div className="work-order-copy">
                <p className="work-order-meta">
                  {order.urgency_label} · {formatTime(order.created_at)}
                </p>
                <h2>{order.title}</h2>
                <p className="work-order-detail">
                  {order.unit
                    ? `${order.address} · Unit ${order.unit}`
                    : order.address}
                </p>
                <p className="work-order-detail">
                  {order.name} | {order.email}
                </p>
                <p className="work-order-detail">{order.description}</p>
              </div>
              <button
                className="dispatch-btn"
                type="button"
                onClick={dispatchTeam}
                disabled={pipelineRunning || booked}
              >
                {dispatchLabel}
              </button>
            </>
          ) : (
            <div className="work-order-empty">
              <div>
                <h2>Loading order…</h2>
              </div>
            </div>
          )}
        </section>

        <section className="team-section" aria-label="Dispatch agents">
          <div className="team-header">
            <h2>Team</h2>
          </div>

          <div className="agent-grid">
          {agents.map((agent) => {
            const canStop = agent.status === 'running';
            const canShowResults = agent.status === 'done' && hasResults(agent.id);

            return (
              <article className="agent-card" key={agent.id}>
                <div className="agent-card-top">
                  <h3>{agent.name}</h3>
                  <span
                    className={`status-dot ${agent.status}`}
                    aria-label={agent.status}
                  />
                </div>

                <div className="agent-body">
                  <p className="agent-action" title={agent.action}>
                    {agent.action}
                  </p>
                  <p className="agent-meta" title={agent.meta}>
                    {agent.meta}
                  </p>
                </div>

                {/* Reserved in every card so the link appearing never shifts the layout. */}
                <div className="agent-link-slot">
                  {agent.id === 'outreach' && liveViewUrl && (
                    <button
                      className="verify-link verify-link-btn"
                      type="button"
                      onClick={() => setShowLiveView(true)}
                    >
                      Watch browser live
                    </button>
                  )}
                  {agent.id === 'caller' && waitingCall && callIndex !== null && orderId && (
                    <>
                      {joinedCall !== callIndex && (
                        <button
                          className="verify-link verify-link-btn"
                          type="button"
                          onClick={() => setJoinedCall(callIndex)}
                        >
                          {liveVoice ? 'Join live call' : 'Join call'}
                        </button>
                      )}
                      {!liveVoice && (
                        <button
                          className="verify-link verify-link-btn subtle-link"
                          type="button"
                          title="Skip the live call and simulate this one"
                          onClick={() =>
                            void simulateCall(orderId, callIndex)
                              .then((snap) => setCaller(snap))
                              .catch(() => undefined)
                          }
                        >
                          Simulate
                        </button>
                      )}
                    </>
                  )}
                </div>

                {canShowResults ? (
                  <button
                    className="stop-btn"
                    type="button"
                    onClick={() => setOpenPanel(agent.id)}
                  >
                    Results
                  </button>
                ) : (
                  <button
                    className="stop-btn"
                    type="button"
                    onClick={() => stopAgent(agent.id)}
                    disabled={!canStop}
                  >
                    Stop
                  </button>
                )}
              </article>
            );
          })}
          </div>
        </section>
      </main>

      {joinedCall !== null && orderId && caller?.calls[joinedCall] && (
        <Suspense fallback={null}>
          <CallModal
            orderId={orderId}
            index={joinedCall}
            contractorName={caller.calls[joinedCall].name}
            onClose={() => setJoinedCall(null)}
          />
        </Suspense>
      )}

      {showLiveView && liveViewUrl && (
        <ResultsModal
          eyebrow="Outreach"
          title="Browserbase live view"
          className="live-view-modal"
          onClose={() => setShowLiveView(false)}
          headerExtra={
            <a
              className="verify-link"
              href={liveViewUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open in new tab ↗
            </a>
          }
        >
          <iframe
            className="live-view-frame"
            src={liveViewUrl}
            title="Browserbase live browser"
            sandbox="allow-same-origin allow-scripts"
            allow="clipboard-read; clipboard-write"
          />
        </ResultsModal>
      )}

      {openPanel === 'scout' && scout && scout.fixers.length > 0 && (
        <ResultsModal
          eyebrow="Scout"
          title="Available fixers"
          onClose={() => setOpenPanel(null)}
          headerExtra={
            order && (
              <a
                className="verify-link"
                href={mapsVerifyUrl(
                  scout.trades.length
                    ? scout.trades
                    : scout.fixers.map((fixer) => fixer.trade),
                  order.address,
                )}
                target="_blank"
                rel="noreferrer"
              >
                Verify 5 closest on Maps
              </a>
            )
          }
        >
          <div className="scout-results">
            {scout.fixers.map((fixer) => {
              const suggested = fixer.rank === 1;
              return (
                <article
                  className={
                    suggested ? 'fixer-card fixer-card-suggested' : 'fixer-card'
                  }
                  key={fixer.id}
                >
                  <div className="fixer-card-top">
                    <div>
                      <p className="fixer-rank">
                        {suggested ? 'Suggested' : `#${fixer.rank}`}
                      </p>
                      <h4>{fixer.name}</h4>
                      <p className="fixer-trade">
                        {fixer.trade} · {fixer.miles} mi
                      </p>
                    </div>
                    <div className="fixer-eta">
                      <strong>{fixer.earliest}</strong>
                      <span>earliest</span>
                    </div>
                  </div>
                  <p className="fixer-why">{fixer.why}</p>
                </article>
              );
            })}
          </div>
        </ResultsModal>
      )}

      {openPanel === 'outreach' && outreach && (
        <ResultsModal
          eyebrow="Outreach"
          title="Quotes"
          onClose={() => setOpenPanel(null)}
          headerExtra={
            <p className="results-summary">
              {outreach.summary}{' '}
              {outreach.send_mode === 'browserbase'
                ? `Sent from ${outreach.from_email} via Browserbase.`
                : 'Sending was simulated (Browserbase not configured).'}
            </p>
          }
        >
          <div className="quote-list">
            {outreach.quotes.map((quote) => {
              const best = quote.fixer_id === outreach.best_fixer_id;
              return (
                <article
                  className={`fixer-card quote-card${best ? ' fixer-card-suggested' : ''}${
                    quote.reply.status !== 'quoted' ? ' quote-card-muted' : ''
                  }`}
                  key={quote.fixer_id}
                >
                  <div className="fixer-card-top">
                    <div>
                      <p className="fixer-rank">{best ? 'Best quote' : quote.trade}</p>
                      <h4>{quote.name}</h4>
                      <p className="fixer-trade fixer-email">to {quote.sent.to}</p>
                    </div>
                    <div className="fixer-eta">
                      <strong>{quoteStatusLabel(quote)}</strong>
                      {'reply_after_s' in quote.reply && (
                        <span>replied in {quote.reply.reply_after_s}s</span>
                      )}
                    </div>
                  </div>
                  {'message' in quote.reply && (
                    <p className="fixer-why">“{quote.reply.message}”</p>
                  )}
                  <details className="email-details">
                    <summary>Email sent</summary>
                    <p className="email-subject">{quote.sent.subject}</p>
                    <pre className="email-body">{quote.sent.body}</pre>
                  </details>
                </article>
              );
            })}
          </div>
        </ResultsModal>
      )}

      {openPanel === 'caller' && caller && (
        <ResultsModal
          eyebrow="Caller"
          title={caller.booking ? 'Booking confirmed' : 'No call placed'}
          onClose={() => setOpenPanel(null)}
          headerExtra={<p className="results-summary">{caller.summary}</p>}
        >
          {caller.booking && (
            <div className="booking-card">
              <div>
                <span>Fixer</span>
                <strong>{caller.booking.name}</strong>
              </div>
              <div>
                <span>Arrival</span>
                <strong>{caller.booking.window}</strong>
              </div>
              <div>
                <span>Estimate</span>
                <strong>${caller.booking.price}</strong>
              </div>
              <div>
                <span>Reference</span>
                <strong>{caller.booking.confirmation}</strong>
              </div>
            </div>
          )}
          {(caller.calls?.length ? caller.calls : []).map((call) => (
            <div className="call-block" key={call.fixer_id}>
              <p className="call-block-title">
                Called {call.name}
                {call.mode === 'voice' ? ' · live voice' : ' · simulated'}
                {call.analyzed_by === 'bedrock' ? ' · analyzed by Bedrock' : ''}
                {call.status === 'skipped' ? ' · skipped' : ''}
                {call.status === 'failed' ? ' · failed' : ''}
                {call.status === 'done' && call.confirmed === false ? ' · not confirmed' : ''}
                {caller.booking?.fixer_id === call.fixer_id ? ' · booked' : ''}
              </p>
              <div className="transcript" aria-label={`Call with ${call.name}`}>
                {call.transcript.map((line, index) => (
                  <div className={`transcript-line ${line.speaker}`} key={index}>
                    <span className="transcript-speaker">
                      {line.speaker === 'caller' ? 'Caller' : call.name}
                    </span>
                    <p>{line.text}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {caller.booking && (
            <p className="results-footnote">
              Booking is ready — use Follow up on the Orders board to email the
              tenant.
            </p>
          )}
        </ResultsModal>
      )}
    </AppShell>
  );
}
