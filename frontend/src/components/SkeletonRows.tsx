export default function SkeletonRows({ count }: { count: number }) {
  return (
    <div className="skeleton-stack">
      {Array.from({ length: count }, (_, index) => (
        <span className="skeleton-row" key={index} />
      ))}
    </div>
  );
}
