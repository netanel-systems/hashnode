---
title: "Stop Reaching for Redux: When useState and useReducer Are All You Need"
subtitle: "Most React state problems are solved by the hooks already in your bundle."
tags: ["reactjs", "javascript", "web-development"]
cover_image_alt: "Diagram comparing useState and useReducer decision flow in React"
status: draft
slug: test-writer-pair-sync
author: klement_gunndu
date: 2026-02-26
---

# Stop Reaching for Redux: When useState and useReducer Are All You Need

Every React project reaches the moment: state gets complicated, and someone suggests adding a state management library. Before you do, consider that React ships two hooks that handle the vast majority of state problems.

## When useState Is Enough

`useState` handles independent, simple values. A modal toggle, a form input, a loading flag. Each call manages one concern.

```jsx
function SearchBar() {
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSearch = async () => {
    setIsLoading(true);
    const results = await fetchResults(query);
    setIsLoading(false);
  };

  return (
    <input value={query} onChange={(e) => setQuery(e.target.value)} />
  );
}
```

Multiple `useState` calls are standard practice. Split state by concern -- only group values into an object when they change together. This keeps re-renders targeted: React only updates components affected by the specific state that changed.

## When to Reach for useReducer

`useReducer` earns its place when state transitions depend on previous state, or when multiple fields update in a single action.

```jsx
function reducer(state, action) {
  switch (action.type) {
    case 'submit':
      return { ...state, isSubmitting: true, error: null };
    case 'success':
      return { ...state, isSubmitting: false, data: action.payload };
    case 'error':
      return { ...state, isSubmitting: false, error: action.payload };
    default:
      throw new Error(`Unknown action: ${action.type}`);
  }
}

function Form() {
  const [state, dispatch] = useReducer(reducer, {
    data: null,
    isSubmitting: false,
    error: null,
  });

  // dispatch({ type: 'submit' }) triggers a predictable transition
}
```

The reducer function is pure -- it takes current state and an action, returns next state. This makes transitions testable in isolation, outside the component.

## The Decision Rule

**Use `useState`** for independent values with simple updates (toggles, inputs, counters). **Use `useReducer`** when state fields are interdependent or transitions follow a state machine pattern. **Reach for external libraries** only when state must be shared across distant components that Context alone makes unwieldy.

Most components never need more than `useState`. Start there. Upgrade only when the code tells you to.

---

Follow @klement_gunndu for more React and AI engineering content. We're building in public.
