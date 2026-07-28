/**
 * ThemeToggle — stub component.
 * Implementation (reading/writing the "dark" class on <html>) comes later.
 */
export default function ThemeToggle() {
  return (
    <button
      type="button"
      aria-label="Toggle theme"
      className="rounded-md p-2 text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      onClick={() => {
        // TODO: implement theme switching
        document.documentElement.classList.toggle('dark');
      }}
    >
      Theme
    </button>
  );
}
