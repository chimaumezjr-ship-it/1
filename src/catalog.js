// Product catalog for KPI Fitness.
//
// Each membership tier is its own Stripe Product (so Checkout and invoices show
// the tier name on each line). Monthly/annual billing are Prices on the same
// Product. Prices are addressed by `lookup_key`, so application code never
// hard-codes price IDs and the catalog can be re-seeded per sandbox.
//
// Amounts are in pence (GBP). These are PLACEHOLDERS — replace them with your
// real membership pricing before running `npm run seed` against live mode.

export const CURRENCY = 'gbp';

export const MEMBERSHIPS = [
  {
    key: 'essential',
    name: 'KPI Essential Membership',
    description: 'Gym floor access and monthly progress check-in.',
    prices: { monthly: 3500, annual: 35000 },
  },
  {
    key: 'performance',
    name: 'KPI Performance Membership',
    description: 'Unlimited classes, quarterly body-composition and KPI assessments.',
    prices: { monthly: 5500, annual: 55000 },
  },
  {
    key: 'elite',
    name: 'KPI Elite Membership',
    description: 'Everything in Performance plus monthly 1:1 coaching and programming.',
    prices: { monthly: 9500, annual: 95000 },
  },
];

// One-off services billed through Stripe Invoicing (personal training blocks,
// assessments, corporate sessions). Also one Product each.
export const SERVICES = [
  {
    key: 'pt_session',
    name: 'Personal Training Session (60 min)',
    unitAmount: 4500,
  },
  {
    key: 'kpi_assessment',
    name: 'KPI Performance Assessment',
    unitAmount: 6000,
  },
  {
    key: 'group_session',
    name: 'Corporate / Team Group Session',
    unitAmount: 15000,
  },
];

export const INTERVALS = { monthly: 'month', annual: 'year' };

export function membershipLookupKey(planKey, interval) {
  return `kpi_${planKey}_${interval}`;
}

export function serviceLookupKey(serviceKey) {
  return `kpi_service_${serviceKey}`;
}
