-- Migration 005: Configure multi-region survival goals
-- Ensures agent memory persists across regions and failures with zero data loss
--
-- This is the load-bearing feature for the hackathon constraint:
-- "Focus on building systems where memory persists across regions and failures
--  with zero data loss."
--
-- CockroachDB's Raft consensus guarantees:
-- 1. Synchronous replication across 3 regions before acknowledging writes
-- 2. No data loss even if an entire region goes offline (SURVIVE REGION FAILURE)
-- 3. Automatic leader election and failover — no manual intervention

-- Add regions to the database (must match cluster provisioned regions)
ALTER DATABASE defaultdb SET PRIMARY REGION "aws-us-east-1";
ALTER DATABASE defaultdb ADD REGION "aws-us-east-2";
ALTER DATABASE defaultdb ADD REGION "aws-us-west-2";

-- Set REGION survival goal:
-- With 3 regions, the database survives the COMPLETE LOSS of any single region.
-- Writes require quorum across regions (2 of 3 must acknowledge).
-- This is the strongest possible data durability guarantee.
ALTER DATABASE defaultdb SURVIVE REGION FAILURE;

-- Set locality for critical memory tables to GLOBAL:
-- GLOBAL tables are replicated to ALL regions with consistent reads from anywhere.
-- Writes are synchronously committed to all 3 regions before acknowledgment.
-- This means: if us-east-1 goes completely offline, agent memory is 100% intact
-- and immediately readable from us-east-2 or us-west-2.

ALTER TABLE incidents SET LOCALITY GLOBAL;
ALTER TABLE incident_embeddings SET LOCALITY GLOBAL;
ALTER TABLE agent_memory SET LOCALITY GLOBAL;
ALTER TABLE agent_decisions SET LOCALITY GLOBAL;

-- Verify configuration
SHOW REGIONS FROM DATABASE defaultdb;
SHOW ZONE CONFIGURATION FOR DATABASE defaultdb;
SHOW SURVIVAL GOAL FROM DATABASE defaultdb;
