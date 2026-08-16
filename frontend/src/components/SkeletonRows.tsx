export default function SkeletonRows({ count }: { count: number }) {
  return (
    <div className="skeleton-stack" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <span className="loader-block skeleton-row" key={index} />
      ))}
    </div>
  );
}
