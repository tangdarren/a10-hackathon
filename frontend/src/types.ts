export type ProblemType =
  | 'no_hot_water'
  | 'leak'
  | 'no_heat_ac'
  | 'appliance'
  | 'lock_access'
  | 'electrical'
  | 'other';

export type Urgency = 'can_wait' | 'soon' | 'emergency';

export type WorkOrder = {
  id: string;
  created_at: string;
  problem: ProblemType;
  problem_label: string;
  problem_other: string | null;
  description: string;
  unit: string;
  name: string;
  phone: string | null;
  urgency: Urgency;
  urgency_label: string;
  building: string;
  title: string;
  location: string;
};

export type WorkOrderCreate = {
  problem: ProblemType;
  problem_other?: string;
  description: string;
  unit: string;
  name: string;
  phone?: string;
  urgency: Urgency;
  building?: string;
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
