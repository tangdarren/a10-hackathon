import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchWorkOrders, followUpTenant } from './api';
import AppShell from './AppShell';
import type { Urgency, WorkOrder } from './types';
import './OverviewPage.css';

const BUCKETS: { key: Urgency; title: string }[] = [
  { key: 'emergency', title: 'Emergency' },
  { key: 'soon', title: 'Soon' },
  { key: 'can_wait', title: 'Can wait' },
];

type OrderGroup = {
  key: string;
  title: string;
  orders: WorkOrder[];
};

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  });
}

function groupOrders(orders: WorkOrder[]): OrderGroup[] {
  const groups = new Map<string, OrderGroup>();

  for (const order of orders) {
    const key =
      order.problem === 'other'
        ? `other:${order.title.toLowerCase()}`
        : order.problem;
    const existing = groups.get(key);
    if (existing) {
      existing.orders.push(order);
    } else {
      groups.set(key, {
        key,
        title: order.title,
        orders: [order],
      });
    }
  }

  return [...groups.values()].map((group) => ({
    ...group,
    orders: [...group.orders].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    ),
  }));
}

export default function OverviewPage() {
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [followUpBusy, setFollowUpBusy] = useState<string | null>(null);
  const [followUpNote, setFollowUpNote] = useState('');

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const next = await fetchWorkOrders();
        if (!cancelled) setOrders(next);
      } catch {
        if (!cancelled) setOrders([]);
      }
    };

    void load();
    const interval = window.setInterval(() => void load(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  const bookedOrders = useMemo(
    () =>
      orders
        .filter((order) => order.status === 'booked' && order.booking)
        .sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        ),
    [orders],
  );

  const openOrders = useMemo(
    () => orders.filter((order) => order.status !== 'booked'),
    [orders],
  );

  const buckets = useMemo(
    () =>
      BUCKETS.map((bucket) => {
        const bucketOrders = openOrders.filter(
          (order) => order.urgency === bucket.key,
        );
        return {
          ...bucket,
          count: bucketOrders.length,
          groups: groupOrders(bucketOrders),
        };
      }),
    [openOrders],
  );

  const emergencies = useMemo(
    () => openOrders.filter((order) => order.urgency === 'emergency').length,
    [openOrders],
  );

  const onFollowUp = async (order: WorkOrder) => {
    setFollowUpBusy(order.id);
    setFollowUpNote('');
    try {
      const result = await followUpTenant(order.id);
      setOrders((current) =>
        current.map((item) => (item.id === result.order.id ? result.order : item)),
      );
      setFollowUpNote(
        `Emailed ${result.to}: ${result.order.booking?.fixer_name ?? 'helper'} coming ${result.order.booking?.expected_label ?? ''} · ~$${result.order.booking?.price ?? '?'}`
      );
    } catch (err) {
      setFollowUpNote(
        err instanceof Error ? err.message : 'Could not send follow-up',
      );
    } finally {
      setFollowUpBusy(null);
    }
  };

  return (
    <AppShell>
      <main className="overview">
        <section className="booked-section" aria-label="Booked helpers">
          <div className="overview-intro">
            <h2>Booked</h2>
            <div className="stats-inline">
              <span>
                <strong>{bookedOrders.length}</strong> booked
              </span>
            </div>
          </div>
          <div className="booked-strip">
            <div className="booked-list">
              {bookedOrders.length === 0 ? (
                <p className="booked-empty">No bookings yet</p>
              ) : (
                bookedOrders.map((order) => {
                  const booking = order.booking!;
                  return (
                    <article key={order.id} className="booked-card">
                      <div className="booked-copy">
                        <p className="booked-title">
                          {order.title}
                          <span className="booked-meta">
                            {' '}
                            · <strong>${booking.price}</strong> ·{' '}
                            {booking.expected_label}
                          </span>
                        </p>
                        <p className="booked-place">
                          {order.name}
                          {' · '}
                          {order.address}
                          {order.unit ? ` · Unit ${order.unit}` : ''}
                        </p>
                      </div>
                      <button
                        type="button"
                        className="follow-up-btn"
                        disabled={
                          order.follow_up_sent || followUpBusy === order.id
                        }
                        onClick={() => void onFollowUp(order)}
                      >
                        {order.follow_up_sent
                          ? 'Followed up'
                          : followUpBusy === order.id
                            ? 'Sending…'
                            : 'Follow up'}
                      </button>
                    </article>
                  );
                })
              )}
            </div>
            {followUpNote && (
              <p className="booked-note" role="status">
                {followUpNote}
              </p>
            )}
          </div>
        </section>

        <div className="overview-intro">
          <h2>Orders</h2>
          <div className="stats-inline" aria-label="Order stats">
            <span>
              <strong>{openOrders.length}</strong> open
            </span>
            <span>
              <strong>{emergencies}</strong>{' '}
              {emergencies === 1 ? 'problem' : 'problems'}
            </span>
          </div>
        </div>

        <div className="priority-grid">
          {buckets.map((bucket) => (
            <section
              key={bucket.key}
              className={`priority-bucket priority-${bucket.key}`}
              aria-label={bucket.title}
            >
              <header className="bucket-header">
                <h3>{bucket.title}</h3>
                <span className="bucket-count">{bucket.count}</span>
              </header>

              <div className="bucket-list">
                {bucket.groups.length === 0 ? (
                  <p className="bucket-empty">None</p>
                ) : (
                  bucket.groups.map((group) => {
                    const primary = group.orders[0];
                    const stacked = group.orders.length > 1;

                    return (
                      <Link
                        key={group.key}
                        to={`/orders/${primary.id}`}
                        className={
                          stacked ? 'order-summary stacked' : 'order-summary'
                        }
                        aria-label={
                          stacked
                            ? `${group.title}, ${group.orders.length} related orders`
                            : group.title
                        }
                      >
                        {stacked && (
                          <>
                            <span className="stack-layer layer-2" aria-hidden="true" />
                            <span className="stack-layer layer-1" aria-hidden="true" />
                          </>
                        )}
                        <div className="order-summary-face">
                          <div className="order-summary-top">
                            <strong>{group.title}</strong>
                            {stacked && (
                              <span className="stack-badge">
                                {group.orders.length}
                              </span>
                            )}
                          </div>
                          <div className="order-summary-meta">
                            <span>{formatTime(primary.created_at)}</span>
                            <span
                              className={`status-pill status-${primary.status}`}
                            >
                              {primary.status_label}
                            </span>
                          </div>
                        </div>
                      </Link>
                    );
                  })
                )}
              </div>
            </section>
          ))}
        </div>
      </main>
    </AppShell>
  );
}
