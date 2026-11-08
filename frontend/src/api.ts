import type { WorkOrder, WorkOrderCreate } from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    let detail = 'Request failed';
    try {
      const body = (await response.json()) as { detail?: string };
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function fetchLatestWorkOrder() {
  return request<WorkOrder | null>('/api/work-orders/latest');
}

export function fetchWorkOrders() {
  return request<WorkOrder[]>('/api/work-orders');
}

export function fetchWorkOrder(id: string) {
  return request<WorkOrder>(`/api/work-orders/${id}`);
}

export function seedWorkOrders() {
  return request<{ seeded: number }>('/api/work-orders/seed', {
    method: 'POST',
  });
}

export function createWorkOrder(payload: WorkOrderCreate) {
  return request<WorkOrder>('/api/work-orders', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function clearWorkOrders() {
  return request<{ cleared: number }>('/api/work-orders', {
    method: 'DELETE',
  });
}

export function fetchDemoLink() {
  return request<{ origin: string; report_url: string; board_url: string }>(
    '/api/demo-link',
  );
}
