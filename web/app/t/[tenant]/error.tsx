'use client';
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main className="state-page"><h1>Fixture loading failed</h1><p>Please retry loading the fixture workspace.</p><button onClick={reset}>Retry</button></main>;
}
