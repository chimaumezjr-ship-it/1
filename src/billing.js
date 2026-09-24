import { CURRENCY, INTERVALS, MEMBERSHIPS, membershipLookupKey, serviceLookupKey } from './catalog.js';

// Tag Checkout Sessions so this flow can be tracked and compared in the
// Dashboard. The random 8-letter suffix is fixed for this flow.
const MEMBERSHIP_CHECKOUT_ID = 'kpi-membership-checkout-vqmrdzlk';

// Only turn this on after you have added an active UK VAT registration in
// Dashboard > Tax > Registrations. Without one, Stripe silently collects £0.
export const automaticTaxEnabled = () => process.env.STRIPE_AUTOMATIC_TAX === 'true';

function badRequest(message) {
  return Object.assign(new Error(message), { status: 400 });
}

export async function priceByLookupKey(stripe, lookupKey) {
  const { data } = await stripe.prices.list({ lookup_keys: [lookupKey], active: true, limit: 1 });
  if (!data.length) {
    throw new Error(`No active price with lookup_key "${lookupKey}". Run \`npm run seed\`.`);
  }
  return data[0];
}

// One Stripe Customer per member, created up front so every webhook event
// resolves to a Customer ID we already have on file.
export async function getOrCreateCustomer({ stripe, store }, { userId, email, name }) {
  const existing = store.getMember(userId);
  if (existing?.stripeCustomerId) return existing.stripeCustomerId;

  const customer = await stripe.customers.create(
    { email, name },
    { idempotencyKey: `kpi-customer-${userId}` },
  );
  store.upsertMember(userId, { email, name, stripeCustomerId: customer.id });
  return customer.id;
}

export async function createMembershipCheckout(ctx, { userId, email, name, plan, interval, baseUrl }) {
  const { stripe } = ctx;
  if (!MEMBERSHIPS.some((m) => m.key === plan)) throw badRequest(`Unknown plan "${plan}"`);
  if (!INTERVALS[interval]) throw badRequest(`Unknown interval "${interval}"`);

  const customer = await getOrCreateCustomer(ctx, { userId, email, name });
  const price = await priceByLookupKey(stripe, membershipLookupKey(plan, interval));

  const tax = automaticTaxEnabled();
  // Do not pass payment_method_types: Stripe picks eligible methods dynamically
  // from your Dashboard settings (cards, Apple Pay, Google Pay, Bacs Debit, ...).
  return stripe.checkout.sessions.create({
    mode: 'subscription',
    customer,
    client_reference_id: userId,
    line_items: [{ price: price.id, quantity: 1 }],
    allow_promotion_codes: true,
    integration_identifier: MEMBERSHIP_CHECKOUT_ID,
    ...(tax && {
      automatic_tax: { enabled: true },
      customer_update: { address: 'auto', name: 'auto' },
      billing_address_collection: 'required',
    }),
    success_url: `${baseUrl}/success.html?session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${baseUrl}/`,
  });
}

// Stripe-hosted page where members update cards, switch plans, download
// invoices and cancel. Configure allowed actions in Dashboard > Settings >
// Billing > Customer portal.
export async function createPortalSession(ctx, { userId, baseUrl }) {
  const member = ctx.store.getMember(userId);
  if (!member?.stripeCustomerId) throw badRequest('No Stripe customer for this member');
  return ctx.stripe.billingPortal.sessions.create({
    customer: member.stripeCustomerId,
    return_url: `${baseUrl}/`,
  });
}

// Stripe Invoicing: bill a one-off service (PT block, assessment, corporate
// session). The customer gets an email with a hosted payment page and PDF.
//
// items: [{ service: 'pt_session', quantity: 10 }] for catalog services, or
//        [{ description: 'Custom programme', amount: 12000 }] in pence.
export async function createAndSendInvoice(ctx, { email, name, items, daysUntilDue = 14, memo, reference }) {
  const { stripe } = ctx;
  if (!items?.length) throw badRequest('At least one invoice item is required');

  const existing = await stripe.customers.list({ email, limit: 1 });
  const customer = existing.data[0] ?? (await stripe.customers.create({ email, name }));

  const idem = (suffix) => (reference ? { idempotencyKey: `kpi-invoice-${reference}-${suffix}` } : undefined);

  const invoice = await stripe.invoices.create(
    {
      customer: customer.id,
      currency: CURRENCY,
      collection_method: 'send_invoice',
      days_until_due: daysUntilDue,
      // Only include the items added below, not stray pending items.
      pending_invoice_items_behavior: 'exclude',
      description: memo,
      ...(automaticTaxEnabled() && { automatic_tax: { enabled: true } }),
    },
    idem('invoice'),
  );

  for (const [i, item] of items.entries()) {
    const params = { customer: customer.id, invoice: invoice.id };
    if (item.service) {
      const price = await priceByLookupKey(stripe, serviceLookupKey(item.service));
      Object.assign(params, { pricing: { price: price.id }, quantity: item.quantity ?? 1 });
    } else if (Number.isInteger(item.amount) && item.description) {
      Object.assign(params, { amount: item.amount, currency: CURRENCY, description: item.description });
    } else {
      throw badRequest(`Invoice item ${i} needs either "service" or integer "amount" + "description"`);
    }
    await stripe.invoiceItems.create(params, idem(`item-${i}`));
  }

  await stripe.invoices.finalizeInvoice(invoice.id);
  return stripe.invoices.sendInvoice(invoice.id);
}
