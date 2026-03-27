-- Migration: Create Reddit intelligence tables
-- Run against: osdbckbblcdtwtnjqmii.supabase.co

-- Table 1: reddit_ad_signals (ad presence data)
CREATE TABLE IF NOT EXISTS public.reddit_ad_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    company_domain TEXT NOT NULL,
    company_name TEXT NOT NULL,
    is_advertiser BOOLEAN DEFAULT FALSE,
    ad_count INTEGER DEFAULT 0,
    promoted_posts_found INTEGER DEFAULT 0,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    raw_response JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_reddit_ad_signal UNIQUE (user_id, company_domain)
);

CREATE INDEX IF NOT EXISTS idx_reddit_ad_signals_user ON public.reddit_ad_signals(user_id);
CREATE INDEX IF NOT EXISTS idx_reddit_ad_signals_domain ON public.reddit_ad_signals(company_domain);

-- Table 2: reddit_organic_signals (organic mentions/sentiment)
CREATE TABLE IF NOT EXISTS public.reddit_organic_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    company_domain TEXT NOT NULL,
    company_name TEXT NOT NULL,
    mention_count INTEGER DEFAULT 0,
    sentiment_score FLOAT DEFAULT 0.0, -- -1.0 to 1.0
    sentiment_label TEXT DEFAULT 'neutral', -- positive, negative, neutral
    positive_mentions INTEGER DEFAULT 0,
    negative_mentions INTEGER DEFAULT 0,
    subreddit_count INTEGER DEFAULT 0,
    total_upvotes INTEGER DEFAULT 0,
    total_comments INTEGER DEFAULT 0,
    last_post_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_reddit_organic_signal UNIQUE (user_id, company_domain)
);

CREATE INDEX IF NOT EXISTS idx_reddit_organic_signals_user ON public.reddit_organic_signals(user_id);
CREATE INDEX IF NOT EXISTS idx_reddit_organic_signals_domain ON public.reddit_organic_signals(company_domain);

-- Add reddit columns to intent_scores (if table exists)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'reddit_ad_active') THEN
        ALTER TABLE public.intent_scores ADD COLUMN reddit_ad_active BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'reddit_organic_active') THEN
        ALTER TABLE public.intent_scores ADD COLUMN reddit_organic_active BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'reddit_sentiment') THEN
        ALTER TABLE public.intent_scores ADD COLUMN reddit_sentiment TEXT DEFAULT 'neutral';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'intent_scores' AND column_name = 'reddit_intensity') THEN
        ALTER TABLE public.intent_scores ADD COLUMN reddit_intensity INTEGER DEFAULT 0;
    END IF;
END $$;
