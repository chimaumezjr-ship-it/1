import fs from 'node:fs';
import path from 'node:path';

// Minimal JSON-file store so the example runs with no database. Swap this for
// your real member database; the interface is intentionally small.
//
// Members are keyed by your own user ID and linked to exactly one Stripe
// Customer. Webhook events are resolved to that Customer (the ownership
// boundary), then mapped back to the member here.
export class JsonStore {
  constructor(file) {
    this.file = file;
    this.data = { members: {}, processedEvents: [] };
    if (file && fs.existsSync(file)) {
      this.data = JSON.parse(fs.readFileSync(file, 'utf8'));
    }
  }

  save() {
    if (!this.file) return;
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(this.file, JSON.stringify(this.data, null, 2));
  }

  getMember(userId) {
    return this.data.members[userId] ?? null;
  }

  findMemberByCustomer(customerId) {
    return Object.values(this.data.members).find((m) => m.stripeCustomerId === customerId) ?? null;
  }

  upsertMember(userId, fields) {
    this.data.members[userId] = { ...this.data.members[userId], userId, ...fields };
    this.save();
    return this.data.members[userId];
  }

  updateMemberByCustomer(customerId, fields) {
    const member = this.findMemberByCustomer(customerId);
    if (!member) return null;
    return this.upsertMember(member.userId, fields);
  }

  // Stripe can deliver an event more than once; record IDs to stay idempotent.
  hasProcessed(eventId) {
    return this.data.processedEvents.includes(eventId);
  }

  markProcessed(eventId) {
    this.data.processedEvents.push(eventId);
    if (this.data.processedEvents.length > 5000) this.data.processedEvents.shift();
    this.save();
  }
}
