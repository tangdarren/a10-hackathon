import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchWorkOrders } from './api';
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

function unitLabel(orders: WorkOrder[]) {
  const units = orders.map((order) => order.unit);
  if (units.length === 1) return `Unit ${units[0]}`;
  if (units.length === 2) return `Units ${units[0]}, ${units[1]}`;
  return `${units.length} units`;
}

export default function OverviewPage() {
  const [orders, setOrders] = useState<WorkOrder[]>([]);

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

  const buckets = useMemo(
    () =>
      BUCKETS.map((bucket) => {
        const bucketOrders = orders.filter(
          (order) => order.urgency === bucket.key,
        );
        return {
          ...bucket,
          count: bucketOrders.length,
          groups: groupOrders(bucketOrders),
        };
      }),
    [orders],
  );

  const emergencies = useMemo(
    () => orders.filter((order) => order.urgency === 'emergency').length,
    [orders],
  );

  return (
    <AppShell>
      <main className="overview">
        <div className="overview-intro">
          <h2>Orders</h2>
          <div className="stats-inline" aria-label="Order stats">
            <span>
              <strong>{orders.length}</strong> open
            </span>
            <span>
              <strong>{emergencies}</strong> emergency
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
                          <span className="order-summary-meta">
                            {unitLabel(group.orders)} ·{' '}
                            {formatTime(primary.created_at)}
                          </span>
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
