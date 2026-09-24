import { test } from 'node:test';
import assert from 'node:assert/strict';
import Stripe from 'stripe';
import { handleEvent } from '../src/webhooks.js';
import { JsonStore } from '../src/store.js';

const quiet = { info() {}, warn() {}, error() {} };

function fakeStripe(subscription) {
  return { subscriptions: { retrieve: async (id) => ({ ...subscription, id }) } };
}

function makeSub(status, overrides = {}) {
  return {
    id: 'sub_1',
    customer: 'cus_1',
    status,
    cancel_at_period_end: false,
    items: { data: [{ price: { lookup_key: 'kpi_performance_monthly' }, current_period_end: 1790000000 }] },
    ...overrides,
  };
}

function setup(status = 'active') {
  const store = new JsonStore(null);
  store.upsertMember('user_1', { email: 'a@example.com', stripeCustomerId: 'cus_1' });
  return { store, stripe: fakeStripe(makeSub(status)), log: quiet };
}

const evt = (id, type, object) => ({ id, type, created: 1, data: { object } });

test('subscription created grants access and records plan', async () => {
  const ctx = setup('active');
  await handleEvent(evt('evt_1', 'customer.subscription.created', { id: 'sub_1' }), ctx);
  const m = ctx.store.getMember('user_1');
  assert.equal(m.hasAccess, true);
  assert.equal(m.plan, 'kpi_performance_monthly');
  assert.equal(m.currentPeriodEnd, 1790000000);
});

test('subscription deleted revokes access', async () => {
  const ctx = setup('canceled');
  await handleEvent(evt('evt_2', 'customer.subscription.deleted', { id: 'sub_1' }), ctx);
  assert.equal(ctx.store.getMember('user_1').hasAccess, false);
});

test('unpaid checkout session is not fulfilled', async () => {
  const ctx = setup('active');
  let retrieved = false;
  ctx.stripe.subscriptions.retrieve = async () => ((retrieved = true), makeSub('active'));
  await handleEvent(
    evt('evt_3', 'checkout.session.completed', { mode: 'subscription', payment_status: 'unpaid', subscription: 'sub_1', customer: 'cus_1' }),
    ctx,
  );
  assert.equal(retrieved, false);
  assert.equal(ctx.store.getMember('user_1').hasAccess, undefined);
});

test('invoice.payment_failed flags member and syncs past_due', async () => {
  const ctx = setup('past_due');
  await handleEvent(
    evt('evt_4', 'invoice.payment_failed', {
      id: 'in_1',
      customer: 'cus_1',
      parent: { subscription_details: { subscription: 'sub_1' } },
    }),
    ctx,
  );
  const m = ctx.store.getMember('user_1');
  assert.equal(m.paymentIssue, 'invoice_payment_failed');
  assert.equal(m.subscriptionStatus, 'past_due');
  assert.equal(m.hasAccess, false);
});

test('one-off invoice.paid clears payment issue without touching subscription', async () => {
  const ctx = setup();
  ctx.store.updateMemberByCustomer('cus_1', { paymentIssue: 'invoice_overdue' });
  ctx.stripe.subscriptions.retrieve = async () => assert.fail('should not fetch subscription');
  await handleEvent(evt('evt_5', 'invoice.paid', { id: 'in_2', customer: 'cus_1', amount_paid: 45000, parent: null }), ctx);
  const m = ctx.store.getMember('user_1');
  assert.equal(m.paymentIssue, null);
  assert.equal(m.lastInvoicePaid.amountPaid, 45000);
});

test('duplicate events are processed once', async () => {
  const ctx = setup('active');
  let calls = 0;
  ctx.stripe.subscriptions.retrieve = async (id) => (calls++, { ...makeSub('active'), id });
  const e = evt('evt_6', 'customer.subscription.updated', { id: 'sub_1' });
  await handleEvent(e, ctx);
  const second = await handleEvent(e, ctx);
  assert.equal(calls, 1);
  assert.equal(second.skipped, true);
});

test('webhook signature verification rejects tampered payloads', () => {
  const stripe = new Stripe('sk_test_dummy');
  const secret = 'whsec_test';
  const payload = JSON.stringify({ id: 'evt_7', type: 'invoice.paid', data: { object: {} } });
  const header = stripe.webhooks.generateTestHeaderString({ payload, secret });
  assert.equal(stripe.webhooks.constructEvent(payload, header, secret).id, 'evt_7');
  assert.throws(() => stripe.webhooks.constructEvent(payload.replace('evt_7', 'evt_8'), header, secret));
});
