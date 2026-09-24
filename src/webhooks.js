// Webhook event handling. Subscription state changes (renewals, failed
// payments, cancellations) happen asynchronously, so this handler — not the
// Checkout success page — is the source of truth for membership access.

const ACCESS_STATUSES = new Set(['active', 'trialing']);

const idOf = (ref) => (typeof ref === 'string' ? ref : ref?.id ?? null);

// Re-fetch the subscription instead of trusting the event snapshot: events can
// arrive out of order, and the latest API state is what access should follow.
async function syncSubscription({ stripe, store, log }, subscriptionId) {
  const sub = await stripe.subscriptions.retrieve(subscriptionId);
  const customerId = idOf(sub.customer);
  const item = sub.items.data[0];

  const member = store.updateMemberByCustomer(customerId, {
    subscriptionId: sub.id,
    subscriptionStatus: sub.status,
    hasAccess: ACCESS_STATUSES.has(sub.status),
    plan: item?.price?.lookup_key ?? null,
    currentPeriodEnd: item?.current_period_end ?? null,
    cancelAtPeriodEnd: sub.cancel_at_period_end,
  });
  if (!member) {
    log.warn(`No member linked to customer ${customerId} (subscription ${sub.id})`);
  }
  return member;
}

function subscriptionIdFromInvoice(invoice) {
  return idOf(invoice.parent?.subscription_details?.subscription);
}

export async function handleEvent(event, ctx) {
  const { stripe, store, log = console } = ctx;

  if (store.hasProcessed(event.id)) {
    return { skipped: true };
  }

  const obj = event.data.object;

  switch (event.type) {
    // Checkout: fulfil only once payment is not 'unpaid'. Delayed payment
    // methods send `completed` while still unpaid, then `async_payment_*`.
    case 'checkout.session.completed':
    case 'checkout.session.async_payment_succeeded': {
      if (obj.mode === 'subscription' && obj.payment_status !== 'unpaid' && obj.subscription) {
        await syncSubscription(ctx, idOf(obj.subscription));
      }
      break;
    }
    case 'checkout.session.async_payment_failed': {
      store.updateMemberByCustomer(idOf(obj.customer), { paymentIssue: 'checkout_payment_failed' });
      break;
    }

    // Subscription lifecycle.
    case 'customer.subscription.created':
    case 'customer.subscription.updated':
    case 'customer.subscription.deleted':
    case 'customer.subscription.paused':
    case 'customer.subscription.resumed': {
      await syncSubscription(ctx, obj.id);
      break;
    }

    // Invoices: both subscription renewals and one-off invoices from
    // Stripe Invoicing (personal training, assessments, corporate sessions).
    case 'invoice.paid': {
      const subscriptionId = subscriptionIdFromInvoice(obj);
      if (subscriptionId) {
        await syncSubscription(ctx, subscriptionId);
      }
      store.updateMemberByCustomer(idOf(obj.customer), {
        paymentIssue: null,
        lastInvoicePaid: { id: obj.id, amountPaid: obj.amount_paid, paidAt: event.created },
      });
      break;
    }
    case 'invoice.payment_failed': {
      // Stripe's Smart Retries and dunning emails (Dashboard > Billing >
      // Revenue recovery) handle retrying; flag the member so staff can follow up.
      const subscriptionId = subscriptionIdFromInvoice(obj);
      if (subscriptionId) {
        await syncSubscription(ctx, subscriptionId);
      }
      store.updateMemberByCustomer(idOf(obj.customer), {
        paymentIssue: 'invoice_payment_failed',
        lastFailedInvoice: obj.id,
      });
      break;
    }
    case 'invoice.overdue': {
      store.updateMemberByCustomer(idOf(obj.customer), {
        paymentIssue: 'invoice_overdue',
        lastOverdueInvoice: obj.id,
      });
      break;
    }

    default:
      log.info?.(`Unhandled event type ${event.type}`);
  }

  store.markProcessed(event.id);
  return { handled: true };
}

export const WEBHOOK_EVENTS = [
  'checkout.session.completed',
  'checkout.session.async_payment_succeeded',
  'checkout.session.async_payment_failed',
  'customer.subscription.created',
  'customer.subscription.updated',
  'customer.subscription.deleted',
  'customer.subscription.paused',
  'customer.subscription.resumed',
  'invoice.paid',
  'invoice.payment_failed',
  'invoice.overdue',
];
