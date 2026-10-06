package pe.axiz.platform.web;

import java.util.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping;

@RestController
public class RouteCatalogue {
  private final RequestMappingHandlerMapping mappings;

  public RouteCatalogue(
      @org.springframework.beans.factory.annotation.Qualifier("requestMappingHandlerMapping")
          RequestMappingHandlerMapping mappings) {
    this.mappings = mappings;
  }

  @GetMapping("/routes")
  public List<String> routes() {
    return mappings.getHandlerMethods().keySet().stream().map(Object::toString).sorted().toList();
  }
}
