import './globals.css';
import { Instrument_Sans, JetBrains_Mono } from 'next/font/google';
const sans = Instrument_Sans({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-instrument' });
const mono = JetBrains_Mono({ subsets: ['latin'], weight: ['400', '500'], variable: '--font-jetbrains' });
export const metadata = { title: 'Keystone — fixture workspace', description: 'Evidence-backed triage fixture preview.' };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable}`}><head><script dangerouslySetInnerHTML={{ __html: `try{document.documentElement.classList.toggle('light',localStorage.getItem('keystone-theme')==='light')}catch{}` }} /></head><body>{children}</body></html>;
}
