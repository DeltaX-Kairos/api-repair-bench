import {sqliteTable, text, integer, index, uniqueIndex} from 'drizzle-orm/sqlite-core';
export const jobs = sqliteTable('repair_jobs', {
  id: text('id').primaryKey(), owner: text('owner').notNull(),
  status: text('status').notNull(), phase: text('phase').notNull(),
  token: text('token').notNull(), data: text('data').notNull(),
  reserved: integer('reserved').notNull(), active: integer('active'),
  lease: integer('lease').notNull(), created: integer('created').notNull(),
}, t => [index('repair_owner').on(t.owner), uniqueIndex('repair_one_active').on(t.active)]);
