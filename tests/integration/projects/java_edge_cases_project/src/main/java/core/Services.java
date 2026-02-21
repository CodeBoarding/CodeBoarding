package core;

import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.function.Function;
import java.util.function.Predicate;
import java.util.stream.Collectors;

import utils.Helpers;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Services
//   Methods (10): filterAnimals, getNames, createDogs, processAnimals,
//                 describeAll, nameComparator, getAnimalCity,
//                 transform, computeTotal, buildQuery
//
// Edge cases covered:
//   - Lambda expression: animals.stream().filter(predicate)
//   - Method reference (static/instance): Animal::getName, Animal::describe
//   - Method reference (constructor): Dog::new
//   - Anonymous class: new Comparator<Animal>() { ... }
//   - Stream API: stream().filter().map().collect()
//   - Optional chaining: Optional.of(...).map(...).orElse(...)
//   - Generics with bounded type parameter: <T extends Animal>
//   - Generic method: <T, R> List<R> transform(...)
//   - Cross-module call: Helpers.add(), Helpers.clamp()
//   - Builder pattern call: new QueryBuilder().from().where().build()
//
// Expected call edges:
//   computeTotal(int, int, int, int) → Helpers.add(int, int)
//   computeTotal(int, int, int, int) → Helpers.clamp(int, int, int)
//   buildQuery(String)               → QueryBuilder() [constructor]
//   buildQuery(String)               → from(String)
//   buildQuery(String)               → where(String)
//   buildQuery(String)               → orderBy(String)
//   buildQuery(String)               → limit(int)
//   buildQuery(String)               → build()
//
// Package dependencies:
//   core imports utils (via Helpers)
//
// ---

public class Services {

    public static List<Animal> filterAnimals(List<Animal> animals, Predicate<Animal> predicate) {
        return animals.stream().filter(predicate).collect(Collectors.toList());
    }

    public static List<String> getNames(List<Animal> animals) {
        return animals.stream().map(Animal::getName).collect(Collectors.toList());
    }

    public static List<Dog> createDogs(List<String> names) {
        return names.stream().map(Dog::new).collect(Collectors.toList());
    }

    public static void processAnimals(List<Animal> animals) {
        animals.forEach(a -> {
            String s = a.speak();
            String n = a.getName();
            System.out.println(n + ": " + s);
        });
    }

    public static <T extends Animal> String describeAll(List<T> animals) {
        return animals.stream()
                .map(Animal::describe)
                .collect(Collectors.joining(", "));
    }

    public static Comparator<Animal> nameComparator() {
        return new Comparator<Animal>() {
            @Override
            public int compare(Animal a, Animal b) {
                return a.getName().compareTo(b.getName());
            }
        };
    }

    public static String getAnimalCity(Animal animal) {
        return Optional.of(animal)
                .map(Animal::getName)
                .map(String::toUpperCase)
                .orElse("UNKNOWN");
    }

    public static <T, R> List<R> transform(List<T> items, Function<T, R> mapper) {
        return items.stream().map(mapper).collect(Collectors.toList());
    }

    public static int computeTotal(int a, int b, int min, int max) {
        int sum = Helpers.add(a, b);
        return Helpers.clamp(sum, min, max);
    }

    public static String buildQuery(String table) {
        return new QueryBuilder()
                .from(table)
                .where("active = true")
                .orderBy("name")
                .limit(10)
                .build();
    }
}
