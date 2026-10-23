export function Pagination({
  page,
  pageSize,
  total,
  label,
  disabled = false,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  label: string;
  disabled?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <nav className="pagination" aria-label={`${label} pagination`}>
      <div className="pagination-summary">
        <span>
          Page {page} of {pages} ({total} {label.toLowerCase()})
        </span>
        <select
          aria-label="Items per page"
          value={pageSize}
          disabled={disabled}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
        >
          {[10, 20, 50, 100].map((size) => (
            <option key={size} value={size}>
              {size} per page
            </option>
          ))}
        </select>
      </div>
      <div>
        <button
          className="secondary"
          aria-label="First page"
          disabled={disabled || page <= 1}
          onClick={() => onPageChange(1)}
        >
          First
        </button>
        <button
          className="secondary"
          aria-label="Previous page"
          disabled={disabled || page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </button>
        <button
          className="secondary"
          aria-label="Next page"
          disabled={disabled || page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </button>
        <button
          className="secondary"
          aria-label="Last page"
          disabled={disabled || page >= pages}
          onClick={() => onPageChange(pages)}
        >
          Last
        </button>
      </div>
    </nav>
  );
}
