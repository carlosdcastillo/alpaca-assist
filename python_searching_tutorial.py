"""
Python Searching Tutorial
========================

This tutorial covers various searching algorithms and techniques in Python,
from basic linear search to advanced algorithms and real-world applications.
"""
# =============================================================================
# 1. LINEAR SEARCH
# =============================================================================


def linear_search(arr, target):
    """
    Linear search: Check each element sequentially until target is found.
    Time Complexity: O(n)
    Space Complexity: O(1)
    """
    for i in range(len(arr)):
        if arr[i] == target:
            return i  # Return index of found element
    return -1  # Element not found


# Example usage
numbers = [64, 34, 25, 12, 22, 11, 90]
target = 22
result = linear_search(numbers, target)
print(f"Linear search for {target}: Found at index {result}")


# =============================================================================
# 2. BINARY SEARCH
# =============================================================================


def binary_search(arr, target):
    """
    Binary search: Efficiently search in a sorted array by dividing search space in half.
    Time Complexity: O(log n)
    Space Complexity: O(1)

    Note: Array must be sorted!
    """
    left, right = 0, len(arr) - 1

    while left <= right:
        mid = (left + right) // 2

        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1  # Search right half
        else:
            right = mid - 1  # Search left half

    return -1  # Element not found


# Example usage
sorted_numbers = [11, 12, 22, 25, 34, 64, 90]
target = 25
result = binary_search(sorted_numbers, target)
print(f"Binary search for {target}: Found at index {result}")


# =============================================================================
# 3. RECURSIVE BINARY SEARCH
# =============================================================================


def binary_search_recursive(arr, target, left=0, right=None):
    """
    Recursive implementation of binary search.
    Time Complexity: O(log n)
    Space Complexity: O(log n) due to recursion stack
    """
    if right is None:
        right = len(arr) - 1

    if left > right:
        return -1  # Base case: element not found

    mid = (left + right) // 2

    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search_recursive(arr, target, mid + 1, right)
    else:
        return binary_search_recursive(arr, target, left, mid - 1)


# Example usage
result = binary_search_recursive(sorted_numbers, 64)
print(f"Recursive binary search for 64: Found at index {result}")


# =============================================================================
# 4. SEARCHING IN STRINGS
# =============================================================================


def find_substring_naive(text, pattern):
    """
    Naive string searching algorithm.
    Time Complexity: O(n*m) where n=len(text), m=len(pattern)
    """
    n, m = len(text), len(pattern)
    positions = []

    for i in range(n - m + 1):
        if text[i : i + m] == pattern:
            positions.append(i)

    return positions


# Example usage
text = "hello world, hello universe, hello python"
pattern = "hello"
positions = find_substring_naive(text, pattern)
print(f"Pattern '{pattern}' found at positions: {positions}")


def find_substring_builtin(text, pattern):
    """
    Using Python's built-in string methods for searching.
    These are highly optimized and usually preferred.
    """
    positions = []
    start = 0

    while True:
        pos = text.find(pattern, start)
        if pos == -1:
            break
        positions.append(pos)
        start = pos + 1

    return positions


positions = find_substring_builtin(text, pattern)
print(f"Built-in search for '{pattern}': {positions}")


# =============================================================================
# 5. SEARCHING IN DATA STRUCTURES
# =============================================================================

# Searching in lists of dictionaries
students = [
    {"name": "Alice", "age": 20, "grade": "A"},
    {"name": "Bob", "age": 22, "grade": "B"},
    {"name": "Charlie", "age": 19, "grade": "A"},
    {"name": "Diana", "age": 21, "grade": "C"},
]


def find_students_by_grade(students, target_grade):
    """Find all students with a specific grade."""
    return [student for student in students if student["grade"] == target_grade]


def find_student_by_name(students, name):
    """Find a specific student by name."""
    for student in students:
        if student["name"] == name:
            return student
    return None


# Example usage
a_students = find_students_by_grade(students, "A")
print(f"Students with grade A: {[s['name'] for s in a_students]}")

alice = find_student_by_name(students, "Alice")
print(f"Found Alice: {alice}")


# =============================================================================
# 6. USING PYTHON'S BUILT-IN FUNCTIONS
# =============================================================================

# Using 'in' operator (very efficient for membership testing)
fruits = ["apple", "banana", "cherry", "date"]
print(f"Is 'banana' in fruits? {'banana' in fruits}")

# Using index() method (raises ValueError if not found)
try:
    idx = fruits.index("cherry")
    print(f"'cherry' found at index: {idx}")
except ValueError:
    print("'cherry' not found")

# Using count() method
text = "python is awesome, python is powerful"
count = text.count("python")
print(f"Word 'python' appears {count} times")

# Using any() and all() for conditional searching
numbers = [1, 3, 5, 7, 9]
has_even = any(num % 2 == 0 for num in numbers)
all_odd = all(num % 2 == 1 for num in numbers)
print(f"Has even numbers: {has_even}, All odd: {all_odd}")


# =============================================================================
# 7. SEARCHING WITH REGULAR EXPRESSIONS
# =============================================================================

import re


def regex_search_examples():
    """Examples of using regular expressions for pattern searching."""
    text = "Contact us at: john@email.com or call (555) 123-4567"

    # Find email addresses
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    emails = re.findall(email_pattern, text)
    print(f"Found emails: {emails}")

    # Find phone numbers
    phone_pattern = r"\(\d{3}\)\s\d{3}-\d{4}"
    phones = re.findall(phone_pattern, text)
    print(f"Found phone numbers: {phones}")

    # Search with groups
    match = re.search(r"(\w+)@(\w+\.\w+)", text)
    if match:
        username, domain = match.groups()
        print(f"Username: {username}, Domain: {domain}")


regex_search_examples()


# =============================================================================
# 8. ADVANCED SEARCHING TECHNIQUES
# =============================================================================


def binary_search_first_occurrence(arr, target):
    """
    Find the first occurrence of target in a sorted array with duplicates.
    """
    left, right = 0, len(arr) - 1
    result = -1

    while left <= right:
        mid = (left + right) // 2

        if arr[mid] == target:
            result = mid
            right = mid - 1  # Continue searching left for first occurrence
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1

    return result


def binary_search_last_occurrence(arr, target):
    """
    Find the last occurrence of target in a sorted array with duplicates.
    """
    left, right = 0, len(arr) - 1
    result = -1

    while left <= right:
        mid = (left + right) // 2

        if arr[mid] == target:
            result = mid
            left = mid + 1  # Continue searching right for last occurrence
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1

    return result


# Example with duplicates
arr_with_duplicates = [1, 2, 2, 2, 3, 4, 4, 5]
target = 2
first = binary_search_first_occurrence(arr_with_duplicates, target)
last = binary_search_last_occurrence(arr_with_duplicates, target)
print(f"First occurrence of {target}: {first}, Last occurrence: {last}")


# =============================================================================
# 9. SEARCHING IN 2D ARRAYS
# =============================================================================


def search_2d_matrix(matrix, target):
    """
    Search for a target in a 2D matrix where:
    - Each row is sorted left to right
    - First integer of each row is greater than last integer of previous row
    """
    if not matrix or not matrix[0]:
        return False

    rows, cols = len(matrix), len(matrix[0])
    left, right = 0, rows * cols - 1

    while left <= right:
        mid = (left + right) // 2
        mid_value = matrix[mid // cols][mid % cols]

        if mid_value == target:
            return True
        elif mid_value < target:
            left = mid + 1
        else:
            right = mid - 1

    return False


# Example usage
matrix = [
    [1, 4, 7, 11],
    [2, 5, 8, 12],
    [3, 6, 9, 16],
]
print(f"Search for 5 in matrix: {search_2d_matrix(matrix, 5)}")
print(f"Search for 13 in matrix: {search_2d_matrix(matrix, 13)}")


# =============================================================================
# 10. PERFORMANCE COMPARISON
# =============================================================================

import time
import random


def performance_comparison():
    """Compare performance of different searching algorithms."""
    # Create test data
    size = 100000
    sorted_data = list(range(size))
    unsorted_data = sorted_data.copy()
    random.shuffle(unsorted_data)
    target = size // 2

    # Linear search on unsorted data
    start_time = time.time()
    linear_search(unsorted_data, target)
    linear_time = time.time() - start_time

    # Binary search on sorted data
    start_time = time.time()
    binary_search(sorted_data, target)
    binary_time = time.time() - start_time

    # Built-in 'in' operator
    start_time = time.time()
    target in sorted_data
    builtin_time = time.time() - start_time

    print("\nPerformance Comparison (100,000 elements):")
    print(f"Linear Search: {linear_time:.6f} seconds")
    print(f"Binary Search: {binary_time:.6f} seconds")
    print(f"Built-in 'in': {builtin_time:.6f} seconds")


# Uncomment to run performance test
# performance_comparison()


# =============================================================================
# 11. PRACTICAL SEARCH APPLICATIONS
# =============================================================================


class SearchableList:
    """A list that maintains sorted order and provides fast searching."""

    def __init__(self):
        self.data = []

    def insert(self, value):
        """Insert value maintaining sorted order."""
        left, right = 0, len(self.data)

        while left < right:
            mid = (left + right) // 2
            if self.data[mid] < value:
                left = mid + 1
            else:
                right = mid

        self.data.insert(left, value)

    def search(self, value):
        """Binary search for value."""
        return binary_search(self.data, value)

    def __str__(self):
        return str(self.data)


# Example usage
searchable_list = SearchableList()
for value in [5, 2, 8, 1, 9, 3]:
    searchable_list.insert(value)

print(f"Searchable list: {searchable_list}")
print(f"Search for 8: Index {searchable_list.search(8)}")


# =============================================================================
# 12. SEARCH ALGORITHMS SUMMARY
# =============================================================================


def search_algorithms_summary():
    """Summary of when to use different search algorithms."""

    summary = {
        "Linear Search": {
            "Time Complexity": "O(n)",
            "Space Complexity": "O(1)",
            "Best for": "Small datasets, unsorted data",
            "Pros": "Simple, works on any data",
            "Cons": "Slow for large datasets",
        },
        "Binary Search": {
            "Time Complexity": "O(log n)",
            "Space Complexity": "O(1)",
            "Best for": "Large sorted datasets",
            "Pros": "Very fast for sorted data",
            "Cons": "Requires sorted data",
        },
        "Hash Table Search": {
            "Time Complexity": "O(1) average",
            "Space Complexity": "O(n)",
            "Best for": "Frequent lookups, key-value pairs",
            "Pros": "Fastest average case",
            "Cons": "Extra space, hash collisions",
        },
    }

    print("\nSearch Algorithms Summary:")
    print("=" * 50)
    for algo, details in summary.items():
        print(f"\n{algo}:")
        for key, value in details.items():
            print(f"  {key}: {value}")


search_algorithms_summary()


# =============================================================================
# EXERCISES FOR PRACTICE
# =============================================================================

"""
Practice Exercises:

1. Implement a function to find the square root of a number using binary search.

2. Write a function to search for a target in a rotated sorted array.

3. Implement the KMP (Knuth-Morris-Pratt) string searching algorithm.

4. Create a function to find the peak element in an array using binary search.

5. Write a search function that finds the closest element to a target in a sorted array.

6. Implement a function to search in a sorted matrix where rows and columns are sorted.

7. Create a fuzzy search function that finds approximate matches in a list of strings.

8. Implement exponential search algorithm.

9. Write a function to find the median of two sorted arrays using binary search.

10. Create a search function that works with custom comparison functions.
"""

print("\nTutorial completed! Try the exercises above to practice your skills.")
