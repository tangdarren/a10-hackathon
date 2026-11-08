export type AgentId = 'scout' | 'outreach' | 'caller';

export type AgentStatus = 'waiting' | 'running' | 'stopped' | 'done' | 'error';

export type Agent = {
  id: AgentId;
  name: string;
  role: string;
  permission: string;
  status: AgentStatus;
  /** Line 1 — current action; replaced as work progresses. */
  action: string;
  /** Line 2 — secondary info (permission, counts, etc.). */
  meta: string;
};

export type Fixer = {
  id: string;
  rank: number;
  name: string;
  trade: string;
  phone: string;
  email: string;
  rating: number;
  reviews: number;
  miles: number;
  earliest: string;
  earliest_minutes: number;
  available_now: boolean;
  emergency: boolean;
  notes: string;
  why: string;
};

export type ScoutResult = {
  agent: 'scout';
  status: 'done';
  trades: string[];
  steps: string[];
  summary: string;
  fixers: Fixer[];
  ranking_source?: 'bedrock' | 'rules';
};

export type QuoteReply =
  | {
      status: 'quoted';
      price: number;
      arrival_minutes: number;
      window: string;
      message: string;
      reply_after_s: number;
    }
  | { status: 'declined'; message: string; reply_after_s: number }
  | { status: 'waiting' }
  | { status: 'no_reply' };

export type Quote = {
  fixer_id: string;
  name: string;
  trade: string;
  email: string;
  phone: string;
  rating: number;
  scout_rank: number;
  sent: { to: string; subject: string; body: string };
  reply: QuoteReply;
};

export type OutreachEmail = {
  fixer_id: string;
  name: string;
  /** Address actually emailed (contractor's address on the demo domain). */
  to: string;
  real_email: string;
  status: 'queued' | 'sending' | 'sent' | 'failed' | 'cancelled';
  subject?: string;
  body?: string;
  error?: string;
};

export type OutreachResult = {
  agent: 'outreach';
  status: 'running' | 'done' | 'stopped' | 'error';
  steps: string[];
  summary: string;
  draft_source: 'bedrock' | 'template';
  send_mode: 'browserbase' | 'simulated';
  from_email: string;
  /** Browserbase live view while the browser is open. */
  live_view_url: string | null;
  /** "gmail": replies read from the sending inbox; "inbox": via SES; "none": not sent. */
  reply_mode: 'gmail' | 'inbox' | 'none';
  reply_to: string | null;
  reply_tag: string;
  awaiting_replies: boolean;
  reply_deadline: string | null;
  emails: OutreachEmail[];
  quotes: Quote[];
  quoted_count: number;
  best_fixer_id: string | null;
};

export type Booking = {
  fixer_id: string;
  name: string;
  trade: string;
  phone: string;
  window: string;
  arrival_minutes: number;
  expected_at?: string;
  expected_label?: string;
  price: number;
  confirmation: string;
  tenant_notified: boolean;
};

export type CallStatus =
  | 'queued'
  | 'ready'
  | 'in_call'
  | 'analyzing'
  | 'done'
  | 'skipped'
  | 'failed';

export type CallRecord = {
  fixer_id: string;
  name: string;
  trade: string;
  /** The business's own number (from Scout). */
  phone: string;
  /** "voice": live browser call with the ElevenLabs agent. */
  mode?: 'voice' | 'simulated';
  /** Who read the call transcript to decide the outcome. */
  analyzed_by?: 'bedrock' | 'rules';
  status?: CallStatus;
  /** False when the contractor didn't confirm or couldn't be reached. */
  confirmed?: boolean;
  window?: string;
  arrival_minutes?: number;
  price?: number;
  confirmation?: string;
  summary?: string;
  error?: string;
  transcript: { speaker: 'fixer' | 'caller'; text: string }[];
  script_source?: 'bedrock' | 'template' | 'elevenlabs';
};

export type CallerResult = {
  agent: 'caller';
  status: 'running' | 'done' | 'stopped' | 'error';
  voice_mode?: 'elevenlabs' | 'simulated';
  current?: number | null;
  steps: string[];
  summary: string;
  calls: CallRecord[];
  called_count: number;
  booking: Booking | null;
  transcript: { speaker: 'fixer' | 'caller'; text: string }[];
  script_source: 'bedrock' | 'template' | 'elevenlabs' | 'none';
};

/** Generic progress verbs shown while an agent is working. */
export const AGENT_PROGRESS: Record<AgentId, string[]> = {
  scout: ['Searching…', 'Finding…', 'Confirming…'],
  outreach: ['Drafting…', 'Sending…', 'Collecting…'],
  caller: ['Dialing…', 'Confirming…', 'Booking…'],
};

export const INITIAL_AGENTS: Agent[] = [
  {
    id: 'scout',
    name: 'Scout',
    role: 'Finds 5 closest nearby',
    permission: 'local directory + bedrock',
    status: 'waiting',
    action: 'Waiting',
    meta: 'local directory + bedrock',
  },
  {
    id: 'outreach',
    name: 'Outreach',
    role: 'Emails the 3 closest',
    permission: 'send email',
    status: 'waiting',
    action: 'Waiting',
    meta: 'send email',
  },
  {
    id: 'caller',
    name: 'Caller',
    role: 'Calls anyone who replies',
    permission: 'place calls',
    status: 'waiting',
    action: 'Waiting',
    meta: 'place calls',
  },
];
