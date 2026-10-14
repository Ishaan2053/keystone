import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Repository root (parent of web/). Fixture JSON lives in ../api/fixtures,
  // outside the Next project directory, so production file tracing must start there.
  outputFileTracingRoot: path.resolve(process.cwd(), ".."),
  // Explicitly trace the fixture files loaded by the /t/[tenant] pages.
  outputFileTracingIncludes: {
    "/t/[tenant]": ["../api/fixtures/**/*"],
  },
};

export default nextConfig;
