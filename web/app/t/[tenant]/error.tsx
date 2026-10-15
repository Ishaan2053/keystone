'use client';
import { dataSource } from '@/lib/data-source-config';
export default function ErrorPage({ reset }: { reset: () => void }) {
  const live = dataSource() === 'api';
  return <main className="state-page"><h1>{live ? 'Live workspace failed' : 'Fixture loading failed'}</h1><p>{live ? 'The live graph API could not be reached. Fixture data is never substituted in API mode.' : 'Please retry loading the fixture workspace.'}</p><button onClick={reset}>Retry</button></main>;
}
