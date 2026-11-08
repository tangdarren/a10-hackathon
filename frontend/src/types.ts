export type ProblemType =
  | 'no_hot_water'
  | 'leak'
  | 'no_heat_ac'
  | 'appliance'
  | 'lock_access'
  | 'electrical'
  | 'other';

export type Urgency = 'can_wait' | 'soon' | 'emergency';

export type OrderStatus =
  | 'new'
  | 'fixers_found'
  | 'awaiting_replies'
  | 'quotes_in'
  | 'no_quotes'
  | 'booked';

export type OrderBooking = {
  fixer_name: string;
  price: number;
  window: string;
  expected_at: string;
  expected_label: string;
  confirmation: string;
};

export type WorkOrder = {
  id: string;
  created_at: string;
  problem: ProblemType;
  problem_label: string;
  problem_other: string | null;
  description: string;
  address: string;
  unit: string | null;
  name: string;
  email: string;
  urgency: Urgency;
  urgency_label: string;
  title: string;
  location: string;
  status: OrderStatus;
  status_label: string;
  booking: OrderBooking | null;
  follow_up_sent: boolean;
};

export type WorkOrderCreate = {
  problem: ProblemType;
  problem_other?: string;
  description: string;
  address: string;
  unit?: string;
  name: string;
  email: string;
  urgency: Urgency;
};

export const PROBLEM_OPTIONS: { value: ProblemType; label: string }[] = [
  { value: 'no_hot_water', label: 'No hot water' },
  { value: 'leak', label: 'Leak / flooding' },
  { value: 'no_heat_ac', label: 'No heat or A/C' },
  { value: 'appliance', label: 'Broken appliance' },
  { value: 'lock_access', label: 'Lock / access issue' },
  { value: 'electrical', label: 'Electrical problem' },
  { value: 'other', label: 'Something else' },
];

export const URGENCY_OPTIONS: { value: Urgency; label: string; hint: string }[] =
  [
    { value: 'can_wait', label: 'Can wait', hint: 'Not urgent' },
    { value: 'soon', label: 'Need help soon', hint: 'Today if possible' },
    { value: 'emergency', label: 'Emergency', hint: 'Unsafe or flooding' },
  ];

export type ManagedProperty = {
  address: string;
  kind: 'apartment' | 'house';
  label: string;
};

/** Properties this manager covers — residents pick from this list only. */
export const MANAGED_PROPERTIES: ManagedProperty[] = [
  {
    address: '1420 Elm St, Oakland, CA',
    kind: 'apartment',
    label: '1420 Elm St, Oakland, CA — apartments',
  },
  {
    address: '900 Harbor Rd, Oakland, CA',
    kind: 'apartment',
    label: '900 Harbor Rd, Oakland, CA — apartments',
  },
  {
    address: '455 Telegraph Ave, Oakland, CA',
    kind: 'apartment',
    label: '455 Telegraph Ave, Oakland, CA — apartments',
  },
  {
    address: '215 Maple Court, Alameda, CA',
    kind: 'house',
    label: '215 Maple Court, Alameda, CA — house',
  },
];
