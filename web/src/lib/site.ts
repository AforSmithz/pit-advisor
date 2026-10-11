// the distribution has no custom domain, so feeds and share cards point at its own hostname
export const SITE = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://d1lb8m6il734fj.cloudfront.net").replace(/\/$/, "");
