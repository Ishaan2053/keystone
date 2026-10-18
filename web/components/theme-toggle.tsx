'use client';
import { Moon, Sun } from 'lucide-react';
import { Button } from './ui/button';
export function ThemeToggle() {
  function toggle() {
    const light = document.documentElement.classList.toggle('light');
    try {
      localStorage.setItem('keystone-theme', light ? 'light' : 'dark');
    } catch {
      /* Storage is optional. */
    }
  }
  return (
    <Button
      variant="ghost"
      className="theme-toggle"
      aria-label="Toggle color theme"
      onClick={toggle}
    >
      <Sun className="dark-icon" />
      <Moon className="light-icon" />
    </Button>
  );
}
