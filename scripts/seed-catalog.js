// Create (or update) KPI Fitness Products and Prices in the current Stripe
// account. Safe to re-run: Products use stable IDs and Prices are found by
// lookup_key. Run against a sandbox first: `npm run seed`.
import { getStripe } from '../src/stripe.js';
import {
  CURRENCY,
  INTERVALS,
  MEMBERSHIPS,
  SERVICES,
  membershipLookupKey,
  serviceLookupKey,
} from '../src/catalog.js';

const stripe = getStripe();

async function upsertProduct({ id, name, description }) {
  try {
    const existing = await stripe.products.retrieve(id);
    return stripe.products.update(existing.id, { name, description, active: true });
  } catch (err) {
    if (err.code !== 'resource_missing') throw err;
    return stripe.products.create({ id, name, description });
  }
}

async function upsertPrice({ product, lookupKey, unitAmount, recurring }) {
  const { data } = await stripe.prices.list({ lookup_keys: [lookupKey], limit: 1 });
  const current = data[0];
  const sameInterval = (current?.recurring?.interval ?? null) === (recurring?.interval ?? null);
  if (current && current.active && current.unit_amount === unitAmount && sameInterval && current.product === product) {
    return { price: current, changed: false };
  }
  // Prices are immutable. Create a new one and move the lookup_key to it;
  // existing subscribers stay on their old price until migrated.
  const price = await stripe.prices.create({
    product,
    currency: CURRENCY,
    unit_amount: unitAmount,
    lookup_key: lookupKey,
    transfer_lookup_key: true,
    // Prices include VAT (UK convention for consumer pricing).
    tax_behavior: 'inclusive',
    ...(recurring && { recurring }),
  });
  if (current?.active) await stripe.prices.update(current.id, { active: false });
  return { price, changed: true };
}

for (const plan of MEMBERSHIPS) {
  const product = await upsertProduct({
    id: `kpi_membership_${plan.key}`,
    name: plan.name,
    description: plan.description,
  });
  for (const [interval, unitAmount] of Object.entries(plan.prices)) {
    const lookupKey = membershipLookupKey(plan.key, interval);
    const { changed } = await upsertPrice({
      product: product.id,
      lookupKey,
      unitAmount,
      recurring: { interval: INTERVALS[interval] },
    });
    console.log(`${changed ? 'created' : 'ok     '} ${lookupKey}`);
  }
}

for (const service of SERVICES) {
  const product = await upsertProduct({ id: `kpi_service_${service.key}`, name: service.name });
  const lookupKey = serviceLookupKey(service.key);
  const { changed } = await upsertPrice({ product: product.id, lookupKey, unitAmount: service.unitAmount });
  console.log(`${changed ? 'created' : 'ok     '} ${lookupKey}`);
}
