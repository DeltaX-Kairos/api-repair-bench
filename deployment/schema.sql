CREATE TABLE `repair_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`owner` text NOT NULL,
	`status` text NOT NULL,
	`phase` text NOT NULL,
	`token` text NOT NULL,
	`data` text NOT NULL,
	`reserved` integer NOT NULL,
	`active` integer,
	`lease` integer NOT NULL,
	`created` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `repair_owner` ON `repair_jobs` (`owner`);--> statement-breakpoint
CREATE UNIQUE INDEX `repair_one_active` ON `repair_jobs` (`active`);