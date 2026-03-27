-- Migration: Create Meta Ads intelligence tables
-- Run against: osdbckbblcdtwtnjqmii.supabase.co

-- Table 1: meta_ad_signals (one row per company)
CREATE TABLE IF NOT EXISTS public.meta_ad_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    company_domain TEXT NOT NULL,
    company_name TEXT NOT NULL,
    fb_page_id TEXT,
    fb_page_url TEXT,
    is_advertiser BOOLEAN DEFAULT FALSE,
    ad_count INTEGER DEFAULT 0,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    raw_response JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_meta_signal UNIQUE (user_id, company_domain)
);

CREATE INDEX IF NOT EXISTS idx_meta_ad_signals_user ON public.meta_ad_signals(user_id);
CREATE INDEX IF NOT EXISTS idx_meta_ad_signals_domain ON public.meta_ad_signals(company_domain);

-- Table 2: meta_ads (individual ads per company)
CREATE TABLE IF NOT EXISTS public.meta_ads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    meta_ad_signals_id UUID REFERENCES public.meta_ad_signals(id) ON DELETE CASCADE,
    ad_id TEXT NOT NULL,
    page_id TEXT,
    ad_creative_body TEXT,
    ad_creative_link TEXT,
    ad_snapshot_url TEXT,
    ad_status TEXT DEFAULT 'ACTIVE',
    ad_delivery_start TIMESTAMPTZ,
    ad_delivery_end TIMESTAMPTZ,
    is_lead_gen BOOLEAN DEFAULT FALSE,
    is_brand_awareness BOOLEAN DEFAULT FALSE,
    is_conversion BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meta_ads_signal ON public.meta_ads(meta_ad_signals_id);

-- Add meta_ad columns to intent_scores (if table exists)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'meta_ad_active') THEN
        ALTER TABLE public.intent_scores ADD COLUMN meta_ad_active BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'meta_ad_lead_gen') THEN
        ALTER TABLE public.intent_scores ADD COLUMN meta_ad_lead_gen BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'meta_ad_intensity') THEN
        ALTER TABLE public.intent_scores ADD COLUMN meta_ad_intensity INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'meta_ad_recency') THEN
        ALTER TABLE public.intent_scores ADD COLUMN meta_ad_recency INTEGER DEFAULT 0;
    END IF;
END $$;
