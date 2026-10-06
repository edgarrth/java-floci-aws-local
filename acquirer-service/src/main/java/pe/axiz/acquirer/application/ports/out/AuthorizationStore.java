package pe.axiz.acquirer.application.ports.out;

import java.util.*;
import pe.axiz.acquirer.domain.Authorization;

public interface AuthorizationStore {
  record Decision(String status, String code) {}

  Decision reserve(Authorization request, boolean declined);

  Optional<Decision> find(UUID id);
}
