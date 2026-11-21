import { StatusPanel } from "@/components/status-panel";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-2xl space-y-6 px-6 py-16">
      <h1 className="text-3xl font-semibold">Ranker</h1>
      <p>
        Describe what you want, by resume or by chat, and get a short list of
        real, currently open jobs ranked for you, with honest reasons.
      </p>
      <p className="text-sm opacity-70">Walking skeleton: system status</p>
      <StatusPanel />
    </main>
  );
}
