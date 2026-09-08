import { useEffect, useState } from "react";

export type RoutePath =
  | "/"
  | "/opportunities"
  | "/positions"
  | "/exchanges"
  | "/exchanges/coin"
  | "/throughput"
  | "/simulator"
  | "/settings";

const VALID_PATHS = new Set<string>([
  "/",
  "/opportunities",
  "/positions",
  "/exchanges",
  "/exchanges/coin",
  "/throughput",
  "/simulator",
  "/settings",
]);

export function normalizePath(path: string): RoutePath {
  const pathname = path.split("?")[0].replace(/\/+$/, "") || "/";
  return (VALID_PATHS.has(pathname) ? pathname : "/") as RoutePath;
}

export function useRouter() {
  const [currentPath, setCurrentPath] = useState<RoutePath>(() =>
    typeof window !== "undefined" ? normalizePath(window.location.pathname) : "/"
  );
  const [search, setSearch] = useState<string>(() =>
    typeof window !== "undefined" ? window.location.search : ""
  );

  useEffect(() => {
    const onPopState = () => {
      setCurrentPath(normalizePath(window.location.pathname));
      setSearch(window.location.search);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = (to: string) => {
    const [pathPart, searchPart] = to.split("?");
    const normalized = normalizePath(pathPart);
    const fullUrl = searchPart ? `${normalized}?${searchPart}` : normalized;
    if (window.location.pathname + window.location.search !== fullUrl) {
      window.history.pushState({}, "", fullUrl);
      setCurrentPath(normalized);
      setSearch(searchPart ? `?${searchPart}` : "");
      window.scrollTo(0, 0);
    }
  };

  return { currentPath, navigate, queryParams: new URLSearchParams(search) };
}

