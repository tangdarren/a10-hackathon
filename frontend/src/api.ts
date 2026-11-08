import type { CallerResult, OutreachResult, ScoutResult } from './agents';
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

export function sendSampleRequests(interval = 1.5) {
  return request<{ queued: number; interval: number }>(
    `/api/work-orders/sample-batch?interval=${interval}`,
    { method: 'POST' },
  );
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

export function runScout(orderId: string, init?: RequestInit) {
  return request<ScoutResult>(`/api/work-orders/${orderId}/agents/scout`, {
    method: 'POST',
    ...init,
  });
}

export function runOutreach(orderId: string, init?: RequestInit) {
  return request<OutreachResult>(`/api/work-orders/${orderId}/agents/outreach`, {
    method: 'POST',
    ...init,
  });
}

export function fetchOutreach(orderId: string, init?: RequestInit) {
  return request<OutreachResult>(`/api/work-orders/${orderId}/agents/outreach`, init);
}

export function stopOutreach(orderId: string) {
  return request<OutreachResult>(`/api/work-orders/${orderId}/agents/outreach/stop`, {
    method: 'POST',
  });
}

export function runCaller(orderId: string, init?: RequestInit) {
  return request<CallerResult>(`/api/work-orders/${orderId}/agents/caller`, {
    method: 'POST',
    ...init,
  });
}

export function fetchCaller(orderId: string, init?: RequestInit) {
  return request<CallerResult>(`/api/work-orders/${orderId}/agents/caller`, init);
}

export function stopCaller(orderId: string) {
  return request<CallerResult>(`/api/work-orders/${orderId}/agents/caller/stop`, {
    method: 'POST',
  });
}

export function callerSession(orderId: string, index: number) {
  return request<{
    index: number;
    name: string;
    connection_type: 'webrtc' | 'websocket';
    conversation_token?: string;
    signed_url?: string;
    conversation_id?: string | null;
    dynamic_variables: Record<string, string>;
  }>(`/api/work-orders/${orderId}/agents/caller/calls/${index}/session`, {
    method: 'POST',
  });
}

export function completeCall(orderId: string, index: number, conversationId: string) {
  return request<CallerResult>(
    `/api/work-orders/${orderId}/agents/caller/calls/${index}/complete`,
    { method: 'POST', body: JSON.stringify({ conversation_id: conversationId }) },
  );
}

export function simulateCall(orderId: string, index: number) {
  return request<CallerResult>(
    `/api/work-orders/${orderId}/agents/caller/calls/${index}/simulate`,
    { method: 'POST' },
  );
}

export function followUpTenant(orderId: string) {
  return request<{
    sent: boolean;
    to: string;
    name: string;
    tenant_email?: string;
    subject: string;
    body: string;
    send_mode?: string;
    order: WorkOrder;
  }>(`/api/work-orders/${orderId}/follow-up`, { method: 'POST' });
}
