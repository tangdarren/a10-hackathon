import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { fetchWorkOrder } from './api';
import {
  AGENT_STEPS,
  INITIAL_AGENTS,
  type Agent,
  type AgentId,
  type AgentStatus,
} from './agents';
import AppShell from './AppShell';
import type { WorkOrder } from './types';
import './App.css';

function formatTime(value: string | Date) {
  const date = typeof value === 'string' ? new Date(value) : value;
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

export default function DispatchBoard() {
  const { orderId } = useParams<{ orderId: string }>();
  const [order, setOrder] = useState<WorkOrder | null>(null);
  const [error, setError] = useState('');
  const [agents, setAgents] = useState<Agent[]>(() =>
    INITIAL_AGENTS.map((agent) => ({ ...agent, log: [] })),
  );
  const [dispatched, setDispatched] = useState(false);

  const timersRef = useRef<Partial<Record<AgentId, number>>>({});
  const statusRef = useRef<Record<AgentId, AgentStatus>>({
    scout: 'waiting',
    outreach: 'waiting',
    caller: 'waiting',
  });

  const setAgentStatus = (id: AgentId, status: AgentStatus) => {
    statusRef.current[id] = status;
  };

  const clearAgentTimer = (id: AgentId) => {
    const timer = timersRef.current[id];
    if (timer !== undefined) {
      window.clearTimeout(timer);
      delete timersRef.current[id];
    }
  };

  const clearAllTimers = () => {
    (Object.keys(timersRef.current) as AgentId[]).forEach(clearAgentTimer);
  };

  useEffect(() => () => clearAllTimers(), []);

  useEffect(() => {
    if (!orderId) return;

    let cancelled = false;
    clearAllTimers();
    setDispatched(false);
    statusRef.current = {
      scout: 'waiting',
      outreach: 'waiting',
      caller: 'waiting',
    };
    setAgents(INITIAL_AGENTS.map((agent) => ({ ...agent, log: [] })));
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
    if (patch.status) setAgentStatus(id, patch.status);
    setAgents((current) =>
      current.map((agent) => (agent.id === id ? { ...agent, ...patch } : agent)),
    );
  };

  const runAgentStep = (id: AgentId, stepIndex: number) => {
    if (statusRef.current[id] !== 'running') return;

    const steps = AGENT_STEPS[id];
    if (stepIndex >= steps.length) {
      updateAgent(id, {
        status: 'done',
        detail: steps[steps.length - 1],
      });
      return;
    }

    const detail = steps[stepIndex];
    setAgents((current) =>
      current.map((agent) => {
        if (agent.id !== id) return agent;
        return {
          ...agent,
          detail,
          log: [...agent.log, detail].slice(-4),
        };
      }),
    );

    timersRef.current[id] = window.setTimeout(() => {
      runAgentStep(id, stepIndex + 1);
    }, 2200 + Math.random() * 800);
  };

  const startAgent = (id: AgentId) => {
    clearAgentTimer(id);
    updateAgent(id, {
      status: 'running',
      detail: 'Starting…',
      log: [],
    });
    timersRef.current[id] = window.setTimeout(() => runAgentStep(id, 0), 400);
  };

  const dispatchTeam = () => {
    if (!order || dispatched) return;
    setDispatched(true);
    INITIAL_AGENTS.forEach((agent, index) => {
      window.setTimeout(() => startAgent(agent.id), index * 180);
    });
  };

  const stopAgent = (id: AgentId) => {
    if (statusRef.current[id] !== 'running') return;
    clearAgentTimer(id);
    setAgentStatus(id, 'stopped');
    setAgents((current) =>
      current.map((agent) => {
        if (agent.id !== id) return agent;
        return {
          ...agent,
          status: 'stopped',
          detail: 'Stopped by manager',
          log: [...agent.log, 'Stopped by manager'].slice(-4),
        };
      }),
    );
  };

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
              <div>
                <p className="work-order-meta">
                  {order.urgency_label} · {formatTime(order.created_at)}
                </p>
                <h2>{order.title}</h2>
                <p className="work-order-location">{order.location}</p>
                <p className="work-order-detail">
                  {order.name}
                  {order.phone ? ` · ${order.phone}` : ''} — {order.description}
                </p>
              </div>
              <button
                className="dispatch-btn"
                type="button"
                onClick={dispatchTeam}
                disabled={dispatched}
              >
                {dispatched ? 'Team dispatched' : 'Dispatch team'}
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

        <section className="agent-grid" aria-label="Dispatch agents">
          {agents.map((agent) => (
            <article className="agent-card" key={agent.id}>
              <div className="agent-card-top">
                <div>
                  <h3>{agent.name}</h3>
                  <p className="agent-role">{agent.role}</p>
                </div>
                <span
                  className={`status-dot ${agent.status}`}
                  aria-label={agent.status}
                />
              </div>

              <div className="agent-body">
                <p className="agent-status">{agent.detail}</p>
                <p
                  className="agent-permission"
                  title={`Allowed to ${agent.permission}`}
                >
                  Permission: {agent.permission}
                </p>
              </div>

              <button
                className="stop-btn"
                type="button"
                onClick={() => stopAgent(agent.id)}
                disabled={agent.status !== 'running'}
              >
                Stop
              </button>
            </article>
          ))}
        </section>
      </main>
    </AppShell>
  );
}
