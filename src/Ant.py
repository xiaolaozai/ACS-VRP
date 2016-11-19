from math import pow
from threading import *
import random
import logging

logger = logging.getLogger("logger")

class Ant(Thread):
    def __init__(self, ID, colony):
        Thread.__init__(self)
        self.cv = Condition()
        self.id = ID
        self.colony = colony
        self.dead = False
        self.working = False

        self.Beta = 2.0
        self.Q0 = random.randint(45, 98) / 100.0
        self.Rho = 0.1

    def reset(self):
        self.graph = self.colony.graph
        self.delivers = set(self.colony.delivers)
        # TODO: the demand per node may be larger than the capacity of each deliver
        self.demands = list(self.colony.demands)

        self.routes = {}
        self.path_cost = 0
        self.path_mat = [[0 for i in range(0, self.graph.nodes_num)] for i in range(0, self.graph.nodes_num)]
        self.nodes_to_visit = set()
        for i in range(0, self.graph.nodes_num):
            self.nodes_to_visit.add(i)

        self.curr_deliver = None
        self.curr_node = None
        self.curr_path_vec = []
        self.curr_path_cost = 0
        self.curr_path_capacity = 0

    def kill(self):
        self.dead = True
        with self.cv:
            self.working = True
            self.cv.notify()

    def run(self):
        while not self.dead:
            with self.cv:
                self.reset()
                self.cv.wait_for(self.should_work)
                self.run_iteration()
                self.working = False

    def begin_colony(self):
        if self.dead:
            return
        with self.cv:
            self.working = True
            self.cv.notify()

    def should_work(self):
        return self.working

    def run_iteration(self):
        graph = self.colony.graph
        self.find_deliver()
        while not self.end():
            graph.lock.acquire()
            new_node = self.state_transition_rule(self.curr_node)
            if self.check_feasibilty(new_node):
                self.insert_node(new_node)
            else:
                self.find_deliver()
            graph.lock.release()
            self.curr_node = new_node
        # don't forget the last route
        self.find_deliver()

        # local search update
        # use 2-opt heuristic
        for key in self.routes.keys():
            self.routes[key] = self.opt_heuristic(self.routes[key])

        self.update_optimum_routes()

        self.colony.update(self)

        # update global colony
        logger.debug('===========Ant {} terminated==========='.format(self.id))

    def end(self):
        return not self.nodes_to_visit

    def state_transition_rule(self, curr_node):
        graph = self.colony.graph
        q = random.random()
        max_node = -1

        # search in the candidate list first
        candidates_nodes = self.graph.cand_list[curr_node].intersection(self.nodes_to_visit)
        if not candidates_nodes:
            candidates_nodes = self.nodes_to_visit

        if q < self.Q0:
            logger.debug("Exploitation")
            max_val = -1
            val = None

            for node in candidates_nodes:
                if graph.tau(curr_node, node) == 0:
                    raise Exception("tau = 0")

                val = graph.tau(curr_node, node) * pow(graph.etha(curr_node, node), self.Beta)
                if val > max_val:
                    max_val = val
                    max_node = node
        else:
            logger.debug("Exploration")
            sum = 0
            node = -1

            for node in candidates_nodes:
                if graph.tau(curr_node, node) == 0:
                    raise Exception("tau = 0")
                sum += graph.tau(curr_node, node) * pow(graph.etha(curr_node, node), self.Beta)
            if sum == 0:
                raise Exception("sum = 0")

            avg = sum / len(candidates_nodes)

            #print("avg = " + str(avg))

            # random node selected according to the probability distribution
            # TODO check this method
            probability = {}
            pre_probability = 0
            r = random.random()
            #logger.info("r " + str(r))
            for node in candidates_nodes:
                probability[node] = graph.tau(curr_node, node) * pow(graph.etha(curr_node, node), self.Beta) / sum
                pre_probability = pre_probability + probability[node]
                #logger.info("p " + str(pre_probability))
                if pre_probability >= r:
                    max_node = node
                    break

            if max_node == -1:
                max_node = node

        if max_node < 0:
            raise Exception("max_node < 0")
        return max_node

    def check_feasibilty(self, next_node):
        distance = self.graph.delta(self.curr_path_vec[-1], next_node) + self.graph.delta(next_node, self.curr_path_vec[0])
        if self.curr_path_cost + distance > self.curr_deliver.max_distance:
            return False
        next_capacity = self.curr_path_capacity + self.demands[next_node]
        # if the capacity exceeds the max capacity of the deliver return false
        # expects that the demand in that node is larger than the origin max capacity of the deliver
        if next_capacity > self.curr_deliver.max_capacity and self.demands[next_node] <= self.curr_deliver.max_capacity:
            return False
        return True

    def insert_node(self, new_node):
        graph = self.graph

        consume_demand = min(self.curr_deliver.max_capacity - self.curr_path_capacity, self.demands[new_node])
        self.demands[new_node] -= consume_demand
        if self.demands[new_node] == 0:
            self.nodes_to_visit.remove(new_node)

        self.curr_path_cost += graph.delta(self.curr_node, new_node)
        self.curr_path_capacity += consume_demand
        self.curr_path_vec.append(new_node)
        self.path_mat[self.curr_node][new_node] = 1
        # current state of ant
        logger.debug('[Insert]Ant {} : {}'.format(str(self.id), self.curr_path_vec))
        logger.debug('[Insert]cost : {}'.format(self.curr_path_cost))
        self.local_updating_rule(self.curr_node, new_node)
        self.curr_node = new_node

    def find_deliver(self):
        graph = self.graph
        # add new route
        if self.curr_path_vec:
            graph.lock.acquire()
            self.local_updating_rule(self.curr_path_vec[-1], self.curr_path_vec[0])
            graph.lock.release()
            self.curr_path_cost += graph.delta(self.curr_path_vec[-1], self.curr_path_vec[0])
            self.routes[self.curr_deliver.id] = self.curr_path_vec
            self.path_cost += self.curr_path_cost

        self.curr_path_vec = []
        self.curr_path_cost = 0
        self.curr_path_capacity = 0

        if self.end():
            return

        # find next deliver
        self.curr_deliver = self.delivers.pop()
        self.curr_node = self.curr_deliver.pos
        self.curr_path_vec = []
        self.curr_path_vec.append(self.curr_node)
        self.curr_path_cost = 0
        consume_demand = min(self.curr_deliver.max_capacity, self.demands[self.curr_node])
        self.demands[self.curr_node] -= consume_demand
        self.curr_path_capacity += consume_demand
        if self.demands[self.curr_node] == 0:
            self.nodes_to_visit.remove(self.curr_node)

        logger.debug('[Find deliver]Ant {} : {}'.format(str(self.id), self.curr_path_vec))
        logger.debug('[Find deliver]cost : {}'.format(self.path_cost))

    def update_optimum_routes(self):
        self.path_cost = 0
        self.path_mat = [[0 for i in range(0, self.graph.nodes_num)] for i in range(0, self.graph.nodes_num)]

        for deliver in self.routes.keys():
            cost = self.update_optimum_path(self.routes[deliver])
            self.path_cost += cost
            logger.debug("Ant {} Deliver {} : {}".format(self.id, deliver, self.routes[deliver]))
            logger.debug("cost : {}".format(cost))

    def update_optimum_path(self, path_vec):
        sum = 0
        nodes_mat = self.graph.nodes_mat
        for i in range(0, len(path_vec) - 1):
            sum += nodes_mat[path_vec[i]][path_vec[i + 1]]
            self.path_mat[path_vec[i]][path_vec[i + 1]] = 1
        sum += nodes_mat[path_vec[-1]][path_vec[0]]
        self.path_mat[path_vec[-1]][path_vec[0]] = 1
        return sum

    def tour_length(self, path_vec, nodes_mat):
        sum = 0
        for i in range(0, len(path_vec) - 1):
            sum += nodes_mat[path_vec[i]][path_vec[i + 1]]
        sum += nodes_mat[path_vec[len(path_vec) - 1]][path_vec[0]]
        return sum

    # 2-opt heuristic
    def opt_heuristic(self, path_vec):
        graph = self.graph
        l = len(path_vec)
        noChange = False
        while not noChange:
            noChange = True
            for i in range(0, l - 1):
                diff = 0
                diff_j = i
                for j in range(i + 1, l):
                    ori_val = graph.delta(path_vec[i], path_vec[i + 1]) + graph.delta(path_vec[j], path_vec[(j + 1) % l])
                    new_val = graph.delta(path_vec[i], path_vec[j]) + graph.delta(path_vec[i + 1], path_vec[(j + 1) % l])
                    if new_val - ori_val < diff:
                        diff = new_val - ori_val
                        diff_j = j
                j = diff_j
                if j != i:
                    new_path_vec = path_vec[0:i + 1]
                    new_path_vec += path_vec[j:i:-1]
                    new_path_vec += path_vec[j + 1:l]
                    path_vec = new_path_vec
                    noChange = False
        # TODO: check the path mat
        return path_vec

    def local_updating_rule(self, curr_node, next_node):
        graph = self.colony.graph
        val = (1 - self.Rho) * graph.tau(curr_node, next_node) + (self.Rho * graph.tau0)
        graph.update_tau(curr_node, next_node, val)