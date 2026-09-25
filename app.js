const STORAGE_KEY = "gambit-advisory-todos";

const form = document.querySelector("#todo-form");
const input = document.querySelector("#todo-input");
const list = document.querySelector("#todo-list");
const emptyState = document.querySelector("#empty-state");
const emptyTitle = document.querySelector("#empty-title");
const emptyCopy = document.querySelector("#empty-copy");
const taskCount = document.querySelector("#task-count");
const clearCompletedButton = document.querySelector("#clear-completed");
const filterButtons = document.querySelectorAll("[data-filter]");

let todos = loadTodos();
let currentFilter = "all";

function loadTodos() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? JSON.parse(saved) : [];
  } catch (error) {
    console.warn("Could not load saved tasks.", error);
    return [];
  }
}

function saveTodos() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(todos));
}

function visibleTodos() {
  if (currentFilter === "active") return todos.filter((todo) => !todo.completed);
  if (currentFilter === "completed") return todos.filter((todo) => todo.completed);
  return todos;
}

function render() {
  list.replaceChildren();
  const visible = visibleTodos();

  visible.forEach((todo) => {
    const item = document.createElement("li");
    item.className = `todo-item${todo.completed ? " completed" : ""}`;
    item.dataset.id = todo.id;

    const checkbox = document.createElement("input");
    checkbox.className = "todo-check";
    checkbox.type = "checkbox";
    checkbox.checked = todo.completed;
    checkbox.setAttribute("aria-label", `Mark ${todo.text} as complete`);
    checkbox.addEventListener("change", () => toggleTodo(todo.id));

    const label = document.createElement("span");
    label.className = "todo-label";
    label.textContent = todo.text;

    const deleteButton = document.createElement("button");
    deleteButton.className = "delete-button";
    deleteButton.type = "button";
    deleteButton.title = "Delete task";
    deleteButton.setAttribute("aria-label", `Delete ${todo.text}`);
    deleteButton.textContent = "×";
    deleteButton.addEventListener("click", () => deleteTodo(todo.id));

    item.append(checkbox, label, deleteButton);
    list.append(item);
  });

  const activeCount = todos.filter((todo) => !todo.completed).length;
  taskCount.textContent = `${activeCount} ${activeCount === 1 ? "task" : "tasks"} left`;
  clearCompletedButton.disabled = !todos.some((todo) => todo.completed);
  emptyState.hidden = visible.length > 0;

  if (todos.length === 0) {
    emptyTitle.textContent = "Your list is clear.";
    emptyCopy.textContent = "Add a task above to get started.";
  } else if (visible.length === 0) {
    emptyTitle.textContent = currentFilter === "completed" ? "Nothing completed yet." : "You’re all caught up.";
    emptyCopy.textContent = currentFilter === "completed" ? "Completed tasks will appear here." : "No active tasks remain.";
  }
}

function addTodo(text) {
  todos.unshift({ id: crypto.randomUUID(), text, completed: false });
  saveTodos();
  render();
}

function toggleTodo(id) {
  todos = todos.map((todo) => todo.id === id ? { ...todo, completed: !todo.completed } : todo);
  saveTodos();
  render();
}

function deleteTodo(id) {
  todos = todos.filter((todo) => todo.id !== id);
  saveTodos();
  render();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addTodo(text);
  input.value = "";
  input.focus();
});

filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    currentFilter = button.dataset.filter;
    filterButtons.forEach((item) => item.classList.toggle("active", item === button));
    render();
  });
});

clearCompletedButton.addEventListener("click", () => {
  todos = todos.filter((todo) => !todo.completed);
  saveTodos();
  render();
});

render();
