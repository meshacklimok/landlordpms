import type { NextConfig } from "next";

const django = process.env.DJANGO_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
          {
            key: "Permissions-Policy",
            value: "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=(), interest-cohort=()",
          },
        ],
      },
    ];
  },

  async rewrites() {
    return {
      beforeFiles: [
        {
          source: "/",
          missing: [{ type: "cookie", key: "sessionid" }],
          destination: `${django}/`,
        },
      ],
      afterFiles: [],
      fallback: [
        { source: "/:path+/", destination: `${django}/:path+/` },
        { source: "/:path*", destination: `${django}/:path*` },
      ],
    };
  },

  skipTrailingSlashRedirect: true,
};

export default nextConfig;
