-- Migration: Add prospect_id to reddit tables for scoring engine compatibility
-- Run against: osdbckbblcdtwtnjqmii.supabase.co

-- Add prospect_id column to reddit_ad_signals
ALTER TABLE public.reddit_ad_signals ADD COLUMN IF NOT EXISTS prospect_id UUID REFERENCES public.prospects(id) ON DELETE CASCADE;

-- Add prospect_id column to reddit_organic_signals
ALTER TABLE public.reddit_organic_signals ADD COLUMN IF NOT EXISTS prospect_id UUID REFERENCES public.prospects(id) ON DELETE CASCADE;

-- Create indexes on prospect_id for fast lookups
CREATE INDEX IF NOT EXISTS idx_reddit_ad_signals_prospect ON public.reddit_ad_signals(prospect_id);
CREATE INDEX IF NOT EXISTS idx_reddit_organic_signals_prospect ON public.reddit_organic_signals(prospect_id);
