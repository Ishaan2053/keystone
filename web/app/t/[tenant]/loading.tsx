import { dataSource } from '@/lib/data-source';
export default function Loading() {
  return <main className="state-page" role="status">{dataSource() === 'api' ? 'Loading live workspace…' : 'Loading fixture workspace…'}</main>;
}
