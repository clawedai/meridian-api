-- Migration: 004_google_ads_tables.sql
-- Google Ads intelligence tables
-- Created: 2026-03-26

-- Table: google_ads_signals
CREATE TABLE IF NOT EXISTS public.google_ads_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    prospect_id UUID REFERENCES public.prospects(id) ON DELETE CASCADE,
    company_domain TEXT NOT NULL,
    company_name TEXT NOT NULL,
    is_advertiser BOOLEAN DEFAULT FALSE,
    ad_count INTEGER DEFAULT 0,
    campaigns_found INTEGER DEFAULT 0,
    keywords_found INTEGER DEFAULT 0,
    keyword_themes JSONB DEFAULT '[]',
    high_intent_keywords INTEGER DEFAULT 0,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    raw_response JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_google_ad_signal UNIQUE (user_id, company_domain)
);

CREATE INDEX IF NOT EXISTS idx_google_ads_signals_user ON public.google_ads_signals(user_id);
CREATE INDEX IF NOT EXISTS idx_google_ads_signals_domain ON public.google_ads_signals(company_domain);
CREATE INDEX IF NOT EXISTS idx_google_ads_signals_prospect ON public.google_ads_signals(prospect_id);

-- Add google_ad columns to intent_scores
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'google_ad_active') THEN
        ALTER TABLE public.intent_scores ADD COLUMN google_ad_active BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'google_ad_intensity') THEN
        ALTER TABLE public.intent_scores ADD COLUMN google_ad_intensity INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'google_ad_keyword_themes') THEN
        ALTER TABLE public.intent_scores ADD COLUMN google_ad_keyword_themes INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'google_ad_recency') THEN
        ALTER TABLE public.intent_scores ADD COLUMN google_ad_recency INTEGER DEFAULT 0;
    END IF;
END $$;
