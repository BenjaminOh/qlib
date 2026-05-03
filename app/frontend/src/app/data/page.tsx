"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

export default function DataPage() {
  const [market, setMarket] = useState("kospi200");

  const marketsQuery = useQuery({
    queryKey: ["markets"],
    queryFn: api.getMarkets,
  });

  const instrumentsQuery = useQuery({
    queryKey: ["instruments", market],
    queryFn: () => api.getInstruments(market),
    enabled: !!market,
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Market Data</h1>

      {/* Market Selector */}
      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Market
        </label>
        <select
          value={market}
          onChange={(e) => setMarket(e.target.value)}
          className="border rounded-lg px-3 py-2 w-64"
        >
          {marketsQuery.data?.markets.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name} - {m.description}
            </option>
          )) || <option value="kospi200">kospi200</option>}
        </select>
      </div>

      {/* Instruments Table */}
      {instrumentsQuery.isLoading && (
        <p className="text-gray-500">Loading instruments...</p>
      )}
      {instrumentsQuery.error && (
        <p className="text-red-500">
          Failed to load instruments. Is the backend running with KR data?
        </p>
      )}
      {instrumentsQuery.data && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-3 bg-gray-50 border-b text-sm text-gray-600">
            {instrumentsQuery.data.count} instruments in{" "}
            <strong>{market}</strong>
          </div>
          <div className="max-h-[600px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b sticky top-0">
                <tr>
                  <th className="text-left px-4 py-2 font-medium text-gray-600">
                    Code
                  </th>
                  <th className="text-left px-4 py-2 font-medium text-gray-600">
                    Start
                  </th>
                  <th className="text-left px-4 py-2 font-medium text-gray-600">
                    End
                  </th>
                </tr>
              </thead>
              <tbody>
                {instrumentsQuery.data.instruments.map((inst) => (
                  <tr key={inst.code} className="border-b hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono">{inst.code}</td>
                    <td className="px-4 py-2 text-gray-500">
                      {inst.start_time}
                    </td>
                    <td className="px-4 py-2 text-gray-500">
                      {inst.end_time}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
