export type AgentId = 'scout' | 'outreach' | 'caller';

export type AgentStatus = 'waiting' | 'running' | 'stopped' | 'done';

export type Agent = {
  id: AgentId;
  name: string;
  role: string;
  permission: string;
  status: AgentStatus;
  detail: string;
  log: string[];
};

export const INITIAL_AGENTS: Agent[] = [
  {
    id: 'scout',
    name: 'Scout',
    role: 'Finds contractors online',
    permission: 'web search',
    status: 'waiting',
    detail: 'Waiting',
    log: [],
  },
  {
    id: 'outreach',
    name: 'Outreach',
    role: 'Emails for quotes',
    permission: 'send email',
    status: 'waiting',
    detail: 'Waiting',
    log: [],
  },
  {
    id: 'caller',
    name: 'Caller',
    role: 'Calls to confirm timing',
    permission: 'place calls',
    status: 'waiting',
    detail: 'Waiting',
    log: [],
  },
];

/** Simulated task steps per agent — each agent runs on its own timer. */
export const AGENT_STEPS: Record<AgentId, string[]> = {
  scout: [
    'Searching local plumber listings…',
    'Filtering by emergency availability…',
    'Found 6 licensed contractors nearby',
    'Ranked by distance and reviews',
  ],
  outreach: [
    'Drafting quote requests…',
    'Emailing Apex Plumbing…',
    'Emailing Northside Mechanical…',
    'Collected 3 quote replies',
  ],
  caller: [
    'Dialing top quote…',
    'Confirming earliest arrival…',
    'Checking Unit 4B access window…',
    'Booked: Apex can arrive in 45 min',
  ],
};
